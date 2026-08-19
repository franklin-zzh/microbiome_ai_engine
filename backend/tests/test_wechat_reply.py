"""企微回调后台回复链路测试：状态分流后的处理逻辑

- BLOCKED：推送预设安全话术（不调 Dify），日志湖 risk_flag=True；
- Dify 调用失败：回兜底话术；
- Dify 正常：推送回答 + 记录 conversation_id（续聊）+ 日志湖；
- 回调入口：事件类回调（enter_session 等）确认接收但不推送。

网络层 mock；DB 用测试库（conftest 已建表）；Redis 用本地实例，测试后清理。
"""
import asyncio
import inspect
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.agent_cs import router
from app.agent_cs import services as sm
from app.clients.dify_chat_client import DifyChatError
from app.core.config import get_settings
from app.core.redis import get_redis

settings = get_settings()
engine = create_engine(settings.test_database_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _test_db_gen():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _cleanup(session_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cs_chat_logs WHERE session_id=:s"), {"s": session_id})
    get_redis().delete(
        sm.SESSION_DIFY_CONV_KEY.format(session_id=session_id),
        sm.SESSION_STATE_KEY.format(session_id=session_id),
    )


# ---------- 话术 ----------


def test_risk_blocked_reply_default_and_override(monkeypatch):
    monkeypatch.setattr(router.settings, "risk_blocked_reply", "")
    assert "健康风险" in router._risk_blocked_reply()
    monkeypatch.setattr(router.settings, "risk_blocked_reply", "自定义拦截话术")
    assert router._risk_blocked_reply() == "自定义拦截话术"


# ---------- BLOCKED：直接话术，0 LLM ----------


def test_process_and_reply_blocked_pushes_reply_and_logs_risk(monkeypatch):
    monkeypatch.setattr("app.agent_cs.router.get_db", _test_db_gen)
    monkeypatch.setattr(router.settings, "risk_blocked_reply", "")
    session_id = f"test-blocked-{uuid.uuid4()}"
    with patch("app.agent_cs.router.wxkf_client.send_kf_text") as send:
        router._process_and_reply(
            session_id=session_id,
            open_id="open-1",
            open_kf_id="kf-1",
            user_message="我现在剧烈腹痛",
            state=sm.STATE_BLOCKED,
            hit_keyword="剧烈腹痛",
        )
        # 只推话术，不调 Dify
        send.assert_called_once()
        assert send.call_args.args[0] == "kf-1"
        assert send.call_args.args[1] == "open-1"
        assert "健康风险" in send.call_args.args[2]

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT ai_reply, risk_flag, hit_human, meta FROM cs_chat_logs WHERE session_id=:s"),
            {"s": session_id},
        ).mappings().first()
    assert row is not None
    assert row["risk_flag"] == 1
    assert row["hit_human"] == 1
    assert "健康风险" in row["ai_reply"]
    _cleanup(session_id)


def test_process_and_reply_blocked_without_open_kf_id_skips_push(monkeypatch):
    monkeypatch.setattr("app.agent_cs.router.get_db", _test_db_gen)
    session_id = f"test-blocked-nopush-{uuid.uuid4()}"
    with patch("app.agent_cs.router.wxkf_client.send_kf_text") as send:
        router._process_and_reply(
            session_id=session_id, open_id="open-1", open_kf_id="",
            user_message="便血", state=sm.STATE_BLOCKED, hit_keyword="便血",
        )
        send.assert_not_called()
    _cleanup(session_id)


# ---------- NORMAL：Dify 失败兜底 / 成功续聊 ----------


def test_answer_via_dify_failure_falls_back(monkeypatch):
    monkeypatch.setattr(router.settings, "risk_blocked_reply", "")
    session_id = f"test-difyerr-{uuid.uuid4()}"
    with patch("app.agent_cs.router.dify_chat_client.chat_messages", side_effect=DifyChatError("dify down")):
        with patch("app.agent_cs.router.wxkf_client.send_kf_text") as send:
            db = next(_test_db_gen())
            try:
                router._answer_via_dify(db, session_id, "open-1", "kf-1", "你好")
            finally:
                db.close()
            send.assert_called_once()
            assert "系统暂时繁忙" in send.call_args.args[2]

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT ai_reply, meta FROM cs_chat_logs WHERE session_id=:s"),
            {"s": session_id},
        ).mappings().first()
    assert row is not None and "系统暂时繁忙" in row["ai_reply"]
    _cleanup(session_id)


def test_answer_via_dify_success_stores_conversation(monkeypatch):
    session_id = f"test-difyok-{uuid.uuid4()}"
    redis = get_redis()
    conv_key = sm.SESSION_DIFY_CONV_KEY.format(session_id=session_id)
    redis.delete(conv_key)
    with patch(
        "app.agent_cs.router.dify_chat_client.chat_messages",
        return_value={
            "answer": "报告解读：检测指标属于科普性解释…",
            "conversation_id": "conv-x",
            "retrieval_resources": [{"id": "seg-9", "score": 0.88}],
        },
    ):
        with patch("app.agent_cs.router.wxkf_client.send_kf_text") as send:
            db = next(_test_db_gen())
            try:
                router._answer_via_dify(db, session_id, "open-1", "kf-1", "报告怎么看")
            finally:
                db.close()
            send.assert_called_once()
            assert send.call_args.args[2] == "报告解读：检测指标属于科普性解释…"

    # 续聊 ID 已存 Redis（下次消息带上）
    assert redis.get(conv_key) == "conv-x"
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT ai_reply, retrieved_chunks FROM cs_chat_logs WHERE session_id=:s"),
            {"s": session_id},
        ).mappings().first()
    assert row is not None
    assert "科普性解释" in row["ai_reply"]
    assert "seg-9" in (row["retrieved_chunks"] or "")
    _cleanup(session_id)


# ---------- HUMAN_MODE：转人工话术 ----------


def test_process_and_reply_human_mode(monkeypatch):
    monkeypatch.setattr("app.agent_cs.router.get_db", _test_db_gen)
    session_id = f"test-human-{uuid.uuid4()}"
    with patch("app.agent_cs.router.wxkf_client.send_kf_text") as send:
        router._process_and_reply(
            session_id=session_id, open_id="open-1", open_kf_id="kf-1",
            user_message="转人工", state=sm.STATE_HUMAN_MODE, hit_keyword=None,
        )
        send.assert_called_once()
        assert "转接人工" in send.call_args.args[2]
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT hit_human FROM cs_chat_logs WHERE session_id=:s"),
            {"s": session_id},
        ).mappings().first()
    assert row is not None and row["hit_human"] == 1
    _cleanup(session_id)


# ---------- 回调入口：事件类回调只确认不推送 ----------


def test_callback_other_event_ignored_without_push():
    """非 enter_session 的 MsgType=event 回调（如 session_status_change）：回 success，
    不推送、不进状态机

    官方结构：ToUserName=corpid，用户标识在 ExternalUserID，无 FromUserName 字段。
    """
    event_xml = (
        "<xml><ToUserName>wwd5f2644ae2a6a894</ToUserName>"
        "<CreateTime>1786694780</CreateTime>"
        "<MsgType>event</MsgType>"
        "<Event>session_status_change</Event>"
        "<Token>tok</Token>"
        "<OpenKfId>kfcb565b8d785380f0b</OpenKfId>"
        "<ExternalUserID>wm-xxx</ExternalUserID></xml>"
    )

    class FakeCrypt:
        def decrypt_msg(self, *args, **kwargs):
            return event_xml

    request = MagicMock()
    request.body = AsyncMock(return_value=b"<xml><Encrypt>abc</Encrypt></xml>")
    with patch("app.agent_cs.router.WXBizMsgCrypt", return_value=FakeCrypt()):
        with patch("app.agent_cs.router.wxkf_client.send_kf_text") as send:
            resp = asyncio.run(
                router.handle_wechat_message(request, "msg_signature", "timestamp", "nonce")
            )
    assert resp.status_code == 200
    assert resp.body == b"success"
    send.assert_not_called()


def test_callback_enter_session_pushes_welcome():
    """enter_session（用户进入会话）：解析 WelcomeCode 并调度欢迎语推送任务（_push_welcome），不进入状态机"""
    event_xml = (
        "<xml><ToUserName>wwd5f2644ae2a6a894</ToUserName>"
        "<CreateTime>1786694780</CreateTime>"
        "<MsgType>event</MsgType>"
        "<Event>enter_session</Event>"
        "<Token>tok</Token>"
        "<OpenKfId>kfcb565b8d785380f0b</OpenKfId>"
        "<ExternalUserID>wm-xxx</ExternalUserID>"
        "<WelcomeCode>code-abc</WelcomeCode></xml>"
    )

    class FakeCrypt:
        def decrypt_msg(self, *args, **kwargs):
            return event_xml

    request = MagicMock()
    request.body = AsyncMock(return_value=b"<xml><Encrypt>abc</Encrypt></xml>")
    with patch("app.agent_cs.router.WXBizMsgCrypt", return_value=FakeCrypt()):
        with patch("app.agent_cs.router.asyncio.create_task") as create_task:
            resp = asyncio.run(
                router.handle_wechat_message(request, "msg_signature", "timestamp", "nonce")
            )
    assert resp.status_code == 200
    assert resp.body == b"success"
    # 调度的是欢迎语推送任务（_push_welcome）：open_kf_id/touser/welcome_code 取自回调
    create_task.assert_called_once()
    task_coro = create_task.call_args.args[0]
    assert inspect.iscoroutine(task_coro)
    assert task_coro.cr_frame.f_locals["open_kf_id"] == "kfcb565b8d785380f0b"
    assert task_coro.cr_frame.f_locals["touser"] == "wm-xxx"
    assert task_coro.cr_frame.f_locals["welcome_code"] == "code-abc"


def test_push_welcome_with_code_sends_msgmenu_on_event():
    """欢迎语推送（有 welcome_code）：走企微事件响应接口 send_msg_on_event，单条 msgmenu 合并欢迎语+菜单，
    不再降级普通 send_msg（企微规则下用户未发消息时 send_msg 必 95001）"""
    with patch("app.agent_cs.router._try_push") as push, \
            patch("app.agent_cs.router.wxkf_client.send_kf_menu") as menu, \
            patch(
                "app.agent_cs.router.wxkf_client.send_kf_welcome_menu_on_event",
                return_value="msgid-1",
            ) as welcome:
        asyncio.run(router._push_welcome("kf-1", "wm-user", "code-xyz"))

    welcome.assert_called_once()
    assert welcome.call_args.args[0] == "code-xyz"
    head = welcome.call_args.args[1]
    assert "富玛特小助手" in head
    assert "热门问题" in head
    items = welcome.call_args.args[2]
    assert len(items) == 5
    assert [it["id"] for it in items] == ["q_fmt", "q_flow", "q_report", "q_service", "q_human"]
    assert items[0]["content"] == "什么是粪菌移植（FMT）？适合哪些人群？"
    # 不再降级普通 send_msg / send_kf_menu
    push.assert_not_called()
    menu.assert_not_called()


def test_push_welcome_without_code_skips_push():
    """欢迎语推送（无 welcome_code，如 48h 内重复进入会话）：直接跳过，不调任何发送接口"""
    with patch("app.agent_cs.router._try_push") as push, \
            patch("app.agent_cs.router.wxkf_client.send_kf_menu") as menu, \
            patch("app.agent_cs.router.wxkf_client.send_kf_welcome_menu_on_event") as welcome:
        asyncio.run(router._push_welcome("kf-1", "wm-user"))

    push.assert_not_called()
    menu.assert_not_called()
    welcome.assert_not_called()


def test_callback_kf_msg_received_text_enters_reply_chain():
    """微信客服消息回调（官方结构）：MsgType=event/Event=kf_msg_received 包裹，真实类型在
    MsgType2=text；用户标识 ExternalUserID、客服账号 OpenKfId（无 FromUserName 字段），
    应进入状态机分流与后台回复链路，而不是被当事件忽略"""
    msg_xml = (
        "<xml><ToUserName>wwd5f2644ae2a6a894</ToUserName>"
        "<CreateTime>1786694780</CreateTime>"
        "<MsgType>event</MsgType>"
        "<Event>kf_msg_received</Event>"
        "<Token>tok</Token>"
        "<OpenKfId>wkcjQPZwAAkcyTYAuysTSLaVIYMqP7lg</OpenKfId>"
        "<MsgType2>text</MsgType2>"
        "<Content>你好</Content>"
        "<MsgId>msg-123</MsgId>"
        "<ExternalUserID>wmcjQPZwAAnZwCWGefvaCIEGvWfGDccw</ExternalUserID></xml>"
    )

    class FakeCrypt:
        def decrypt_msg(self, *args, **kwargs):
            return msg_xml

    request = MagicMock()
    request.body = AsyncMock(return_value=b"<xml><Encrypt>abc</Encrypt></xml>")
    with patch("app.agent_cs.router.WXBizMsgCrypt", return_value=FakeCrypt()):
        with patch("app.agent_cs.router.get_db") as get_db, \
                patch("app.agent_cs.router.get_redis") as get_redis, \
                patch("app.agent_cs.router._restore_state_from_db", return_value=None), \
                patch(
                    "app.agent_cs.router.sm.route_incoming",
                    return_value={"state": "NORMAL", "hit_keyword": None, "should_answer": True},
                ) as route, \
                patch("app.agent_cs.router._lazy_cleanup_expired"), \
                patch("app.agent_cs.router._persist_route_state") as persist, \
                patch("app.agent_cs.router.asyncio.create_task") as create_task:
            resp = asyncio.run(
                router.handle_wechat_message(request, "msg_signature", "timestamp", "nonce")
            )
    assert resp.status_code == 200
    assert resp.body == b"success"
    # 消息进入了状态机分流（而非被 event 忽略）
    route.assert_called_once()
    assert route.call_args.args[1] == "wxkf:wmcjQPZwAAnZwCWGefvaCIEGvWfGDccw"
    assert route.call_args.args[2] == "你好"
    persist.assert_called_once()
    # 后台回复任务被调度；open_kf_id 必须取自 OpenKfId（而非 ToUserName=corpid）
    create_task.assert_called_once()
    task_coro = create_task.call_args.args[0]
    assert inspect.iscoroutine(task_coro)
    assert task_coro.cr_frame.f_locals["open_kf_id"] == "wkcjQPZwAAkcyTYAuysTSLaVIYMqP7lg"


def test_callback_kf_msg_or_event_schedules_sync():
    """kf_msg_or_event：只通知不携带消息体，应调度 _sync_and_reply_async 并回 success"""
    msg_xml = (
        "<xml><ToUserName>wwd5f2644ae2a6a894</ToUserName>"
        "<CreateTime>1786694780</CreateTime>"
        "<MsgType>event</MsgType>"
        "<Event>kf_msg_or_event</Event>"
        "<Token>tok-123</Token>"
        "<OpenKfId>wkcjQPZwAAkcyTYAuysTSLaVIYMqP7lg</OpenKfId></xml>"
    )

    class FakeCrypt:
        def decrypt_msg(self, *args, **kwargs):
            return msg_xml

    request = MagicMock()
    request.body = AsyncMock(return_value=b"<xml><Encrypt>abc</Encrypt></xml>")
    with patch("app.agent_cs.router.WXBizMsgCrypt", return_value=FakeCrypt()):
        with patch("app.agent_cs.router.asyncio.create_task") as create_task:
            resp = asyncio.run(
                router.handle_wechat_message(request, "msg_signature", "timestamp", "nonce")
            )
    assert resp.status_code == 200
    assert resp.body == b"success"
    # 调度的是 sync 拉取任务，且 open_kfid / token 取自回调 XML
    create_task.assert_called_once()
    task_coro = create_task.call_args.args[0]
    assert inspect.iscoroutine(task_coro)
    assert task_coro.cr_frame.f_locals["open_kfid"] == "wkcjQPZwAAkcyTYAuysTSLaVIYMqP7lg"
    assert task_coro.cr_frame.f_locals["token"] == "tok-123"


def test_sync_and_collect_routes_text_and_dedups(monkeypatch):
    """sync_msg 拉取链路：仅 origin=3 的文本进入状态机分流，且 msgid 幂等去重"""
    monkeypatch.setattr("app.agent_cs.router.get_db", _test_db_gen)
    redis = get_redis()
    msgid = f"sync-{uuid.uuid4()}"
    cursor_key = "wxkf:sync_cursor:kf-sync"
    dedup_key = f"wxkf:synced_msgid:{msgid}"
    redis.delete(cursor_key, dedup_key)

    sync_resp = {
        "errcode": 0, "next_cursor": "cur-1", "has_more": 0,
        "msg_list": [
            {"msgid": msgid, "open_kfid": "kf-sync", "external_userid": "wm-user",
             "send_time": 1, "origin": 3, "msgtype": "text", "text": {"content": "报告怎么看"}},
            # origin=4 系统事件，应被忽略
            {"msgid": "evt-1", "open_kfid": "kf-sync", "external_userid": "",
             "send_time": 2, "origin": 4, "msgtype": "event",
             "event": {"event_type": "enter_session"}},
        ],
    }
    route_ret = {"state": "NORMAL", "hit_keyword": None, "should_answer": True}

    try:
        with patch("app.agent_cs.router.wxkf_client.sync_msg", return_value=sync_resp), \
                patch("app.agent_cs.router._restore_state_from_db", return_value=None), \
                patch("app.agent_cs.router._lazy_cleanup_expired"), \
                patch("app.agent_cs.router._persist_route_state") as persist, \
                patch("app.agent_cs.router.sm.route_incoming", return_value=route_ret) as route, \
                patch("app.agent_cs.router._try_push") as push:
            to_reply, to_welcome = router._sync_and_collect("kf-sync", "tok")

        assert len(to_reply) == 1
        assert to_reply[0]["user_message"] == "报告怎么看"
        assert to_reply[0]["open_id"] == "wm-user"
        assert to_reply[0]["open_kf_id"] == "kf-sync"
        route.assert_called_once()
        persist.assert_called_once()
        # 即时 ack：Dify 思考期间先推「收到」给用户
        push.assert_called_once()
        assert push.call_args.args[0] == "kf-sync"
        assert push.call_args.args[1] == "wm-user"
        assert "收到" in push.call_args.args[2]

        # 第二次拉同一批：msgid 已去重，不再分流
        with patch("app.agent_cs.router.wxkf_client.sync_msg", return_value=sync_resp), \
                patch("app.agent_cs.router._restore_state_from_db", return_value=None), \
                patch("app.agent_cs.router._lazy_cleanup_expired"), \
                patch("app.agent_cs.router._persist_route_state") as persist2, \
                patch("app.agent_cs.router.sm.route_incoming", return_value=route_ret) as route2, \
                patch("app.agent_cs.router._try_push") as push2:
            to_reply2, to_welcome2 = router._sync_and_collect("kf-sync", "tok")
        assert to_reply2 == []
        assert to_welcome2 == []
        route2.assert_not_called()
        push2.assert_not_called()
    finally:
        redis.delete(cursor_key, dedup_key)


def test_process_and_reply_greeting_fast_path(monkeypatch):
    """纯日常问候（你好/在吗/hi）走 Fast-Path：直接下发欢迎语+快捷菜单，0 LLM 且不调用 Dify"""
    monkeypatch.setattr("app.agent_cs.router.get_db", _test_db_gen)
    session_id = f"test-greeting-{uuid.uuid4()}"
    with patch("app.agent_cs.router._try_push") as push_mock, \
            patch("app.agent_cs.router.dify_chat_client.chat_messages") as dify_call:
        router._process_and_reply(
            session_id=session_id,
            open_id="open-greet",
            open_kf_id="kf-greet",
            user_message="你好呀",
            state=sm.STATE_NORMAL,
            hit_keyword=None,
        )
        # 极速秒回：发送菜单与欢迎语，不调用 Dify
        push_mock.assert_called_once()
        assert push_mock.call_args.args[0] == "kf-greet"
        assert push_mock.call_args.args[1] == "open-greet"
        assert "富玛特小助手" in push_mock.call_args.args[2]
        assert "【核心技术】什么是粪菌移植（FMT）？" in push_mock.call_args.args[2]
        dify_call.assert_not_called()

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT ai_reply, meta FROM cs_chat_logs WHERE session_id=:s"),
            {"s": session_id},
        ).mappings().first()
    assert row is not None
    assert "富玛特" in row["ai_reply"]
    assert "greeting_fast_path" in str(row["meta"])
    _cleanup(session_id)


def test_is_pure_greeting_and_menu_recognition():
    """验证问候语与菜单识别函数"""
    assert sm.is_pure_greeting("你好") is True
    assert sm.is_pure_greeting("您好！") is True
    assert sm.is_pure_greeting("hi") is True
    assert sm.is_pure_greeting("在吗？") is True
    assert sm.is_pure_greeting("早上好~") is True
    # 含有具体问题的长句不是纯问候
    assert sm.is_pure_greeting("你好，请问fmt适合什么年龄段？") is False
    assert sm.is_pure_greeting("报告怎么看") is False

    assert sm.is_menu_request("菜单") is True
    assert sm.is_menu_request("常见问题") is True
    assert sm.is_menu_request("menu") is True
    assert sm.is_menu_request("帮助") is True
    assert sm.is_menu_request("什么是肠菌移植") is False



def test_sync_and_collect_collects_enter_session():
    """sync_msg 拉取到 enter_session 事件时，应收集到 to_welcome 并记录日志"""
    redis = get_redis()
    msgid = f"sync-enter-{uuid.uuid4()}"
    cursor_key = "wxkf:sync_cursor:kf-welcome"
    dedup_key = f"wxkf:synced_msgid:{msgid}"
    redis.delete(cursor_key, dedup_key)

    sync_resp = {
        "errcode": 0, "next_cursor": "cur-w", "has_more": 0,
        "msg_list": [
            {
                "msgid": msgid,
                "open_kfid": "kf-welcome",
                "external_userid": "wm-new-user",
                "send_time": 100,
                "origin": 4,
                "msgtype": "event",
                "event": {
                    "event_type": "enter_session",
                    "open_kfid": "kf-welcome",
                    "external_userid": "wm-new-user",
                    "welcome_code": "code-123",
                },
            },
        ],
    }

    try:
        with patch("app.agent_cs.router.wxkf_client.sync_msg", return_value=sync_resp):
            to_reply, to_welcome = router._sync_and_collect("kf-welcome", "tok")

        assert len(to_reply) == 0
        assert len(to_welcome) == 1
        assert to_welcome[0]["open_kf_id"] == "kf-welcome"
        assert to_welcome[0]["touser"] == "wm-new-user"

        # 再次拉取已去重
        with patch("app.agent_cs.router.wxkf_client.sync_msg", return_value=sync_resp):
            to_reply2, to_welcome2 = router._sync_and_collect("kf-welcome", "tok")
        assert len(to_welcome2) == 0
    finally:
        redis.delete(cursor_key, dedup_key)

