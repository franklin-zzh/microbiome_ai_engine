"""企微回调后台回复链路测试：状态分流后的处理逻辑

- BLOCKED：推送预设安全话术（不调 Dify），日志湖 risk_flag=True；
- Dify 调用失败：回兜底话术；
- Dify 正常：推送回答 + 记录 conversation_id（续聊）+ 日志湖。

网络层 mock；DB 用测试库（conftest 已建表）；Redis 用本地实例，测试后清理。
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

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
