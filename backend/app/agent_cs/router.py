"""客服 Agent 业务域：REST 路由

- chat_router：对话日志湖 / 会话状态机（mb_ai_engine.cs_*）
- wechat_router：企微「微信客服」/ 公众号回调入口（5 秒内必须回 success，AI 逻辑异步化）

对话链路（双层风险防护）：
1. 后端规则拦截（第一道，0 LLM 成本）：回调消息先经 ``route_incoming`` 命中
   RISK_KEYWORDS -> BLOCKED，直接推送预设安全话术，不调 Dify；
2. Dify 工作流语义兜底（第二道）：未命中拦截的消息才转发 Dify 客服应用
   （工作流内 risk_guard 用轻量模型捕获隐晦/同义表达）。
"""
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent_cs import services as sm
from app.agent_cs.models import CsChatLog, SessionState, SessionStateValue
from app.agent_cs.schemas import (
    CsChatLogCreate,
    CsChatLogListResponse,
    CsChatLogOut,
    SessionStateOut,
)
from app.clients import dify_chat_client
from app.clients.dify_chat_client import DifyChatError
from app.clients.wx import wxkf_client
from app.clients.wx.wxkf_client import WxKfError
from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import structured_log
from app.core.redis import get_redis
from app.core.security import require_admin, require_internal_key
from app.clients.wx.wxbizmsgcrypt import WXBizMsgCrypt

chat_router = APIRouter(prefix="/chat", tags=["chat"])
wechat_router = APIRouter(tags=["wechat_kf"])
settings = get_settings()

# 预设话术（0 LLM 成本）：
# - 风险拦截：与工作流 risk_handoff 节点话术一致；可用 .env RISK_BLOCKED_REPLY 覆盖
# - 转人工：HUMAN_MODE 下推送
# - 兜底：Dify 调用失败时回复
_DEFAULT_RISK_BLOCKED_REPLY = (
    "您描述的情况可能涉及健康风险，我这边无法在线判断。建议您尽快联系专业医生或拨打客服热线。"
    "我马上为您转接专属健康顾问。"
)
HUMAN_HANDOFF_REPLY = "正在为您转接人工客服，请稍候。"
DIFY_FAILBACK_REPLY = "抱歉，系统暂时繁忙，请稍后再试。"
NON_TEXT_REPLY = "您好，我暂时只能处理文字消息，请用文字描述您的问题～"
# Dify 思考期间的即时 ack 话术（缩短用户感知等待）；可用 .env WXKF_ACK_REPLY 覆盖
_DEFAULT_WXKF_ACK_REPLY = "收到，正在为您查询，请稍候～"
# 用户进入会话（enter_session）时的欢迎语；可用 .env WXKF_WELCOME_REPLY 覆盖
_DEFAULT_WXKF_WELCOME_REPLY = "您好！我是您的「富玛特小助手」"
WXKF_MENU_HEAD = "您可以直接输入您想了解的问题，也可以点击下方热门问题快速咨询："

# 欢迎语附带的快捷提问菜单（msgmenu）：用户点击后自动发送对应文本问题，走正常问答链路
WXKF_MENU_ITEMS = [
    {"id": "q_fmt", "content": "什么是粪菌移植（FMT）？适合哪些人群？"},
    {"id": "q_flow", "content": "肠菌检测怎么做？采样流程是什么？"},
    {"id": "q_report", "content": "检测报告怎么看？多久出结果？"},
    {"id": "q_service", "content": "肠道营养调理与益生菌方案有哪些？"},
    {"id": "q_human", "content": "怎么联系人工客服或健康顾问？"},
]

# 统一的富文本欢迎菜单文本（含微信专属可点击超链接标签）
_DEFAULT_WXKF_WELCOME_MENU_TEXT = (
    "您好！我是您的「富玛特小助手」\n"
    "您可以直接输入您想了解的问题，也可以点击下方热门问题快速咨询：\n\n"
    '<a href="weixin://kefumenu?kefumenucontent=什么是粪菌移植（FMT）？适合哪些人群？&kefumenuid=q_fmt">【核心技术】什么是粪菌移植（FMT）？</a>\n\n'
    '<a href="weixin://kefumenu?kefumenucontent=肠菌检测怎么做？采样流程是什么？&kefumenuid=q_flow">【采样流程】肠菌检测怎么做？流程是什么？</a>\n\n'
    '<a href="weixin://kefumenu?kefumenucontent=检测报告怎么看？多久出结果？&kefumenuid=q_report">【报告解读】检测报告怎么看？多久出结果？</a>\n\n'
    '<a href="weixin://kefumenu?kefumenucontent=肠道营养调理与益生菌方案有哪些？&kefumenuid=q_service">【调理方案】肠道营养调理与益生菌方案有哪些？</a>\n\n'
    '<a href="weixin://kefumenu?kefumenucontent=怎么联系人工客服或健康顾问？&kefumenuid=q_human">【人工咨询】如何联系富玛特人工健康顾问？</a>'
)

# sync_msg 拉取链路的 Redis 键 TTL（与企微消息 3 天窗口对齐）
SYNC_CURSOR_TTL = 3 * 24 * 3600  # 游标与 msgid 去重集合的存活时间



# ============ 对话日志湖 ============


def _write_chat_log(db: Session, **fields) -> CsChatLog:
    """写入一条全量对话日志（Data Lake 闭环入口，内部调用统一走这里）"""
    record = CsChatLog(**fields)
    db.add(record)
    db.commit()
    db.refresh(record)
    structured_log(
        event="cs_chat_log_written",
        item_id=record.id,
        domain="CS",
        source_type="CHAT_LOG",
        status="WRITTEN",
        extra={"session_id": record.session_id, "channel": record.channel.value, "hit_human": record.hit_human},
    )
    return record


@chat_router.post("/logs", response_model=CsChatLogOut, dependencies=[Depends(require_internal_key)])
def create_cs_chat_log(
    body: CsChatLogCreate,
    db: Session = Depends(get_db),
):
    """写入一条全量对话日志（Data Lake 闭环入口）

    仅内部异步任务可写（X-API-Key，INTERNAL_API_KEY）。
    第 5 周 Task 5.1 之后由异步任务自动调用；当前先提供显式 API 便于联调。
    """
    return _write_chat_log(
        db,
        channel=body.channel,
        session_id=body.session_id,
        open_id=body.open_id,
        user_message=body.user_message,
        ai_reply=body.ai_reply,
        retrieved_chunks=body.retrieved_chunks,
        intent=body.intent,
        match_score=body.match_score,
        satisfaction=body.satisfaction,
        hit_human=body.hit_human,
        human_takeover=body.human_takeover,
        risk_flag=body.risk_flag,
        meta=body.meta,
    )


@chat_router.get("/logs", response_model=CsChatLogListResponse, dependencies=[Depends(require_admin)])
def list_cs_chat_logs(
    session_id: str = Query(None),
    channel: str = Query(None, pattern="^(WXKF|MP|H5|WECOM_GROUP)$"),
    hit_human: bool = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(CsChatLog)
    if session_id:
        query = query.filter(CsChatLog.session_id == session_id)
    if channel:
        query = query.filter(CsChatLog.channel == channel)
    if hit_human is not None:
        query = query.filter(CsChatLog.hit_human == hit_human)
    total = query.count()
    items = query.order_by(CsChatLog.created_at.desc()).offset(skip).limit(limit).all()
    return CsChatLogListResponse(total=total, items=items)


# ============ 会话状态机 ============


def _restore_state_from_db(db: Session, redis: Redis, session_id: str) -> Optional[SessionState]:
    """Redis 状态缺失（TTL 过期/重启）时，从 cs_session_state 恢复状态并续 TTL。

    只恢复 HUMAN_MODE / BLOCKED（NORMAL 是默认态，恢复无意义）；
    防止用户沉默超过 TTL 后回来，转人工/拦截状态丢失导致 AI 重新抢答。
    """
    record = db.query(SessionState).filter(SessionState.session_id == session_id).first()
    state_key = sm.SESSION_STATE_KEY.format(session_id=session_id)
    if record is not None and record.state != SessionStateValue.NORMAL and not redis.exists(state_key):
        sm.set_state(redis, session_id, record.state.value)
        structured_log(
            event="session_state_restored_from_db",
            status=record.state.value,
            extra={"session_id": session_id},
        )
    return record


def _lazy_cleanup_expired(db: Session) -> None:
    """惰性清理：删除不活跃超过保留期（session_retention_days）的状态行，LIMIT 防长锁。

    保留语义：expires_at（滚动刷新）= 最后活跃 + TTL；行在 expires_at 早于
    now - 保留期 时才删除（即不活跃满保留期）。历史 NULL 行用 updated_at 兜底。
    """
    cutoff = datetime.now() - timedelta(days=settings.session_retention_days)
    db.execute(
        text(
            "DELETE FROM cs_session_state "
            "WHERE COALESCE(expires_at, updated_at) < :cutoff LIMIT 200"
        ),
        {"cutoff": cutoff},
    )


@chat_router.get("/session/{session_id}", response_model=SessionStateOut, dependencies=[Depends(require_admin)])
def get_session_state(
    session_id: str,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """读取会话状态（Redis 优先；Redis 缺失时展示 DB 中最后持久化的状态）"""
    record = db.query(SessionState).filter(SessionState.session_id == session_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    state = sm.get_state(redis, session_id)
    if not redis.exists(sm.SESSION_STATE_KEY.format(session_id=session_id)):
        state = record.state.value  # Redis 缺失时回退到 DB 最后状态
    return SessionStateOut(
        session_id=record.session_id,
        channel=record.channel.value,
        open_id=record.open_id,
        state=state,
        negative_streak=record.negative_streak,
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
    )


def _persist_route_state(
    db: Session,
    record: Optional[SessionState],
    session_id: str,
    result: dict,
    channel: str,
    open_id: str,
) -> None:
    """持久化状态镜像（存在则更新，不存在则创建）；expires_at 滚动刷新（与 Redis TTL 对齐）"""
    expires_at = datetime.now() + timedelta(seconds=settings.session_ttl_seconds)
    if record is None:
        record = SessionState(
            session_id=session_id,
            channel=channel,
            open_id=open_id,
            state=SessionStateValue(result["state"]),
            expires_at=expires_at,
        )
        db.add(record)
    else:
        record.state = SessionStateValue(result["state"])
        record.expires_at = expires_at
        if result["state"] == sm.STATE_NORMAL:
            record.negative_streak = 0
    db.commit()


@chat_router.post("/session/{session_id}/route", dependencies=[Depends(require_internal_key)])
def route_message(
    session_id: str,
    body: dict,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """入站消息路由（状态机分流）——微信回调异步链路的入口判断

    body: {"user_message": "...", "channel": "WXKF", "open_id": "..."}
    返回该消息的处理路径；消息本体应随后进入队列异步调 Dify。
    """
    user_message = body.get("user_message", "")
    if not user_message:
        raise HTTPException(status_code=422, detail="user_message is required")

    # DB 兜底恢复（必须先于 route_incoming：恢复后的 HUMAN_MODE/BLOCKED 参与本次分流）
    record = _restore_state_from_db(db, redis, session_id)

    result = sm.route_incoming(redis, session_id, user_message)

    # 惰性清理过期状态行（顺带执行，不阻塞主流程）
    _lazy_cleanup_expired(db)

    _persist_route_state(
        db, record, session_id, result,
        channel=body.get("channel", "WXKF"),
        open_id=body.get("open_id", ""),
    )

    return {
        "session_id": session_id,
        "route": result,
        "action": "pass_to_human" if not result["should_answer"] else "queue_for_ai",
    }


@chat_router.post("/session/{session_id}/release", dependencies=[Depends(require_admin)])
def release_session(
    session_id: str,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """人工接管完成 / 管理员重置会话状态 -> NORMAL"""
    sm.release_human(redis, session_id)
    record = db.query(SessionState).filter(SessionState.session_id == session_id).first()
    if record:
        record.reset()
        record.expires_at = datetime.now() + timedelta(seconds=settings.session_ttl_seconds)
        db.commit()
    return {"session_id": session_id, "state": sm.STATE_NORMAL}


# ============ 企微微信客服 / 公众号回调 ============


@wechat_router.get("/wechat/kf/callback")
@wechat_router.get("/wx/msg")
def verify_wechat_url(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """企微客服 URL 校验回调 (GET 请求)

    当在企业微信后台点击“保存设置接收消息服务器”时，企微会发送此请求。
    """
    if not settings.wxkf_token or not settings.wxkf_encoding_aes_key:
        structured_log(
            event="wechat_kf_verify_failed",
            status="FAILED",
            error_msg="WXKF_TOKEN or WXKF_ENCODING_AES_KEY not configured in .env",
        )
        raise HTTPException(status_code=500, detail="WeChat KF credentials not configured in backend")

    try:
        crypt = WXBizMsgCrypt(
            token=settings.wxkf_token,
            encoding_aes_key=settings.wxkf_encoding_aes_key,
            receive_id=settings.wxkf_corp_id,
        )
        reply_echo = crypt.decrypt_echo_str(
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
        )
        structured_log(
            event="wechat_kf_verify_success",
            status="SUCCESS",
            extra={"reply_echo": reply_echo},
        )
        # 企微验证要求必须直接返回解密后的明文文本
        return Response(content=reply_echo, media_type="text/plain")
    except Exception as exc:
        structured_log(
            event="wechat_kf_verify_error",
            status="FAILED",
            error_msg=str(exc),
        )
        raise HTTPException(status_code=400, detail=f"URL Verification Failed: {exc}")


@wechat_router.post("/wechat/kf/callback")
@wechat_router.post("/wx/msg")
async def handle_wechat_message(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """企微客服 接收用户消息回调 (POST 请求)

    P0 安全基线：公网可到达的端点必须先验签+解密，验签失败直接拒绝（400），
    防止伪造消息注入；全部操作毫秒级，满足 5 秒内回 success 的硬约束。

    链路（第一道防线在后端，第二道在 Dify 工作流）：
    验签解密 -> 状态机分流（命中风险关键词直接 BLOCKED，0 LLM 成本）
             -> 回 success -> 后台：拦截话术推送 / 未命中则异步调 Dify 后主动推送。
    """
    if not settings.wxkf_token or not settings.wxkf_encoding_aes_key:
        structured_log(
            event="wechat_kf_msg_failed",
            status="FAILED",
            error_msg="WXKF_TOKEN or WXKF_ENCODING_AES_KEY not configured in .env",
        )
        raise HTTPException(status_code=500, detail="WeChat KF credentials not configured in backend")

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty body")
    if len(raw) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Body too large")
    try:
        root = ET.fromstring(raw)
        encrypt = root.findtext("Encrypt") or ""
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed XML body: {exc}")
    if not encrypt:
        raise HTTPException(status_code=400, detail="Missing Encrypt field")

    try:
        crypt = WXBizMsgCrypt(
            token=settings.wxkf_token,
            encoding_aes_key=settings.wxkf_encoding_aes_key,
            receive_id=settings.wxkf_corp_id,
        )
        # 验签失败抛 ValueError；解密后是内层明文 XML（含 ExternalUserID/OpenKfId/Content 等）
        plain_xml = crypt.decrypt_msg(msg_signature, timestamp, nonce, encrypt)
        msg_root = ET.fromstring(plain_xml)
        event = msg_root.findtext("Event") or ""
        # 微信客服消息回调（kf_msg_received）外层 MsgType 固定为 event，
        # 真实消息类型在 MsgType2（text/image/...）；enter_session 等纯事件无 MsgType2
        if event == "kf_msg_received":
            msg_type = msg_root.findtext("MsgType2") or ""
        else:
            msg_type = msg_root.findtext("MsgType") or ""
        # 微信客服回调没有 FromUserName 字段：用户标识在 ExternalUserID（推送的 touser），
        # 客服账号 open_kfid 在 OpenKfId 标签（ToUserName 是 corp_id，不能用于 kf/send_msg）
        from_user = msg_root.findtext("ExternalUserID") or ""
        open_kf_id = msg_root.findtext("OpenKfId") or ""
        content = msg_root.findtext("Content") or ""
        msg_id = msg_root.findtext("MsgId") or ""
        token = msg_root.findtext("Token") or ""
        # enter_session 事件携带的欢迎语凭据：仅在「用户过去 48 小时未收过欢迎语且未发过消息」时返回
        welcome_code = msg_root.findtext("WelcomeCode") or ""
    except Exception as exc:
        structured_log(
            event="wechat_kf_msg_verify_failed",
            status="FAILED",
            error_msg=str(exc),
            extra={"timestamp": timestamp},
        )
        raise HTTPException(status_code=400, detail=f"Callback verification failed: {exc}")

    # 只记录元数据（日志不存消息原文，个保法）
    structured_log(
        event="wechat_kf_msg_received",
        status="RECEIVED",
        extra={
            "timestamp": timestamp,
            "from_user": from_user,
            "msg_type": msg_type,
            "wechat_event": event,
            "content_len": len(content),
            "msg_id": msg_id,
        },
    )

    # kf_msg_or_event：企微只通知「有新消息/事件」，不携带消息体（from_user/content 均为空），
    # 需用回调里的 Token 调 sync_msg 主动拉取真实消息后再进入状态机/回复链路。
    if event == "kf_msg_or_event":
        structured_log(
            event="wechat_kf_sync_notified",
            status="NOTIFIED",
            extra={"open_kfid": open_kf_id, "has_token": bool(token)},
        )
        asyncio.create_task(_sync_and_reply_async(open_kf_id, token))
        return Response(content="success", media_type="text/plain")

    # enter_session：用户进入会话，用事件携带的 welcome_code 推欢迎语 + 快捷提问菜单（msgmenu）；不进入状态机
    if event == "enter_session":
        structured_log(
            event="wechat_kf_welcome",
            status="SENT",
            extra={"open_kfid": open_kf_id, "from_user": from_user, "has_code": bool(welcome_code)},
        )
        if from_user:
            asyncio.create_task(_push_welcome(open_kf_id, from_user, welcome_code))
        return Response(content="success", media_type="text/plain")

    # 事件类回调（enter_session / kf_msg_sent 等）：仅确认接收，不回复、不进入状态机。
    # 若按非文本消息处理会给用户误推「只能处理文字消息」提示。
    if msg_type == "event":
        structured_log(
            event="wechat_kf_event_ignored",
            status="IGNORED",
            extra={"from_user": from_user, "wechat_event": event, "msg_id": msg_id},
        )
        return Response(content="success", media_type="text/plain")

    # 非文本消息：暂不支持，回提示即可（不进入状态机）
    if msg_type != "text" or not content:
        structured_log(
            event="wechat_kf_msg_ignored",
            status="IGNORED",
            extra={"from_user": from_user, "msg_type": msg_type, "msg_id": msg_id},
        )
        asyncio.create_task(_push_text(open_kf_id, from_user, NON_TEXT_REPLY))
        return Response(content="success", media_type="text/plain")

    session_id = f"wxkf:{from_user}"
    # 状态机分流（Redis + DB 镜像均为毫秒级，满足 5 秒约束）：
    # 第一道防线：命中 RISK_KEYWORDS -> BLOCKED，直接回安全话术，不调 Dify（0 LLM 成本）
    db = next(get_db())
    redis = get_redis()
    record = _restore_state_from_db(db, redis, session_id)
    result = sm.route_incoming(redis, session_id, content)
    _lazy_cleanup_expired(db)
    _persist_route_state(db, record, session_id, result, channel="WXKF", open_id=from_user)

    structured_log(
        event="wechat_kf_route",
        status=result["state"],
        extra={
            "session_id": session_id,
            "hit_keyword": result["hit_keyword"],
            "should_answer": result["should_answer"],
        },
    )

    # 5 秒约束：先回 success；AI 逻辑（拦截话术推送 / Dify 转发）后台执行
    asyncio.create_task(_handle_wechat_reply(
        session_id=session_id,
        open_id=from_user,
        open_kf_id=open_kf_id,
        user_message=content,
        state=result["state"],
        hit_keyword=result["hit_keyword"],
    ))
    return Response(content="success", media_type="text/plain")


# ============ 后台回复链路（异步任务，5 秒约束外执行） ============


def _risk_blocked_reply() -> str:
    """风险拦截话术：.env RISK_BLOCKED_REPLY 可覆盖，默认与工作流 risk_handoff 节点一致"""
    return settings.risk_blocked_reply or _DEFAULT_RISK_BLOCKED_REPLY


def _try_push(open_kf_id: str, touser: str, content: str) -> None:
    """主动推送一条文本；失败只记日志不抛出，保证日志湖/状态机不受影响"""
    if not open_kf_id:
        structured_log(
            event="wxkf_push_skipped",
            status="SKIPPED",
            extra={"touser": touser, "reason": "no open_kf_id"},
        )
        return
    try:
        wxkf_client.send_kf_text(open_kf_id, touser, content)
    except WxKfError as exc:
        structured_log(
            event="wxkf_push_failed",
            status="FAILED",
            error_msg=str(exc),
            extra={"touser": touser, "content_len": len(content)},
        )


async def _push_text(open_kf_id: str, touser: str, content: str) -> None:
    """后台协程推送一条文本（同步网络调用放线程池）"""
    await asyncio.to_thread(_try_push, open_kf_id, touser, content)


async def _push_welcome(
    open_kf_id: str,
    touser: str,
    welcome_code: Optional[str] = None,
) -> None:
    """进入会话欢迎语：仅通过企微事件响应接口（send_msg_on_event + welcome_code）发送单条 msgmenu。

    企微规则（官方文档）：普通 kf/send_msg 只能在用户主动发消息后的 48 小时内回复（最多 5 条），
    用户未发消息时企业主动下发会被拒（95001 send msg count limit）；欢迎语只能走 send_msg_on_event，
    且 welcome_code 仅在「用户过去 48 小时未收过欢迎语且未向客服发过消息」时返回——即同一用户
    48 小时内只欢迎一次。因此：无 welcome_code（48h 内重复进入）直接跳过，不再降级 send_msg。
    """
    if not welcome_code:
        structured_log(
            event="wxkf_welcome_skipped",
            status="SKIPPED",
            extra={
                "open_kf_id": open_kf_id,
                "touser": touser,
                "reason": "no welcome_code (48h 内已欢迎过或用户已发过消息)",
            },
        )
        return

    welcome_text = settings.wxkf_welcome_reply or _DEFAULT_WXKF_WELCOME_REPLY
    full_head = f"{welcome_text}\n\n{WXKF_MENU_HEAD}"
    try:
        msgid = await asyncio.to_thread(
            wxkf_client.send_kf_welcome_menu_on_event,
            welcome_code,
            full_head,
            WXKF_MENU_ITEMS,
            "",
        )
        structured_log(
            event="wxkf_welcome_on_event_sent",
            status="SENT",
            extra={"open_kf_id": open_kf_id, "touser": touser, "msgid": msgid},
        )
    except WxKfError as exc:
        structured_log(
            event="wxkf_welcome_on_event_failed",
            status="FAILED",
            error_msg=str(exc),
            extra={"touser": touser, "code": welcome_code},
        )


def _answer_via_dify(db: Session, session_id: str, open_id: str, open_kf_id: str, user_message: str) -> None:
    """正常路径：调 Dify 客服应用 -> 推送回答 -> 日志湖；失败回兜底话术"""
    redis = get_redis()
    conversation_id = sm.get_dify_conversation(redis, session_id)
    try:
        result = dify_chat_client.chat_messages(
            query=user_message,
            user=open_id,
            conversation_id=conversation_id,
        )
    except DifyChatError as exc:
        structured_log(
            event="dify_chat_failed",
            status="FAILED",
            error_msg=str(exc),
            extra={"session_id": session_id},
        )
        _try_push(open_kf_id, open_id, DIFY_FAILBACK_REPLY)
        _write_chat_log(
            db,
            channel="WXKF",
            session_id=session_id,
            open_id=open_id,
            user_message=user_message,
            ai_reply=DIFY_FAILBACK_REPLY,
            hit_human=False,
            meta={"route": "dify_error"},
        )
        return

    answer = result.get("answer") or DIFY_FAILBACK_REPLY
    _try_push(open_kf_id, open_id, answer)
    if result.get("conversation_id"):
        sm.set_dify_conversation(redis, session_id, result["conversation_id"])
    _write_chat_log(
        db,
        channel="WXKF",
        session_id=session_id,
        open_id=open_id,
        user_message=user_message,
        ai_reply=answer,
        retrieved_chunks=result.get("retrieval_resources") or None,
        meta={"route": "dify", "conversation_id": result.get("conversation_id")},
    )


def _process_and_reply(
    session_id: str,
    open_id: str,
    open_kf_id: str,
    user_message: str,
    state: str,
    hit_keyword: Optional[str],
) -> None:
    """后台同步主流程：按状态分流

    - BLOCKED：推送预设安全话术（0 LLM 调用），日志湖标 risk_flag；
    - HUMAN_MODE：推送转人工话术；
    - NORMAL：调 Dify 客服应用拿回答后推送。
    """
    db = next(get_db())
    try:
        if state == sm.STATE_BLOCKED:
            reply = _risk_blocked_reply()
            _try_push(open_kf_id, open_id, reply)
            _write_chat_log(
                db,
                channel="WXKF",
                session_id=session_id,
                open_id=open_id,
                user_message=user_message,
                ai_reply=reply,
                hit_human=True,
                risk_flag=True,
                meta={"route": "blocked", "hit_keyword": hit_keyword},
            )
        elif state == sm.STATE_HUMAN_MODE:
            _try_push(open_kf_id, open_id, HUMAN_HANDOFF_REPLY)
            _write_chat_log(
                db,
                channel="WXKF",
                session_id=session_id,
                open_id=open_id,
                user_message=user_message,
                ai_reply=HUMAN_HANDOFF_REPLY,
                hit_human=True,
                meta={"route": "human"},
            )
        elif sm.is_pure_greeting(user_message) or sm.is_menu_request(user_message):
            reply_text = _DEFAULT_WXKF_WELCOME_MENU_TEXT
            _try_push(open_kf_id, open_id, reply_text)
            _write_chat_log(
                db,
                channel="WXKF",
                session_id=session_id,
                open_id=open_id,
                user_message=user_message,
                ai_reply=reply_text,
                meta={"route": "greeting_fast_path"},
            )
        else:
            _answer_via_dify(db, session_id, open_id, open_kf_id, user_message)
    except Exception as exc:
        structured_log(
            event="wechat_kf_reply_failed",
            status="FAILED",
            error_msg=str(exc),
            extra={"session_id": session_id, "state": state},
        )
    finally:
        db.close()


async def _handle_wechat_reply(
    session_id: str,
    open_id: str,
    open_kf_id: str,
    user_message: str,
    state: str,
    hit_keyword: Optional[str],
) -> None:
    """后台协程包装：同步网络/DB 逻辑放入线程池，不阻塞事件循环"""
    try:
        await asyncio.to_thread(
            _process_and_reply,
            session_id, open_id, open_kf_id, user_message, state, hit_keyword,
        )
    except Exception as exc:
        structured_log(
            event="wechat_kf_reply_task_error",
            status="FAILED",
            error_msg=str(exc),
            extra={"session_id": session_id},
        )


# ============ kf_msg_or_event：sync_msg 拉取链路（回调只通知不携带消息体） ============


def _handle_synced_event(redis: Redis, open_kfid: str, msg: dict) -> Optional[dict]:
    """处理 sync_msg 拉到的事件类消息（如 enter_session：用户进入会话主动推欢迎语）。

    返回待推欢迎语任务参数 dict（open_kf_id/touser/welcome_code）；不满足条件返回 None。
    """
    if msg.get("msgtype") != "event":
        return None
    event_dict = msg.get("event") or {}
    event_type = event_dict.get("event_type") or ""
    if event_type != "enter_session":
        return None

    external_userid = msg.get("external_userid") or event_dict.get("external_userid") or ""
    kfid = msg.get("open_kfid") or event_dict.get("open_kfid") or open_kfid
    msgid = msg.get("msgid") or ""
    welcome_code = event_dict.get("welcome_code") or ""
    if not external_userid or not kfid:
        return None

    # msgid 幂等去重：防多次拉取重复推欢迎语
    if msgid:
        dedup_key = f"wxkf:synced_msgid:{msgid}"
        if not redis.sadd(dedup_key, 1):
            return None
        redis.expire(dedup_key, SYNC_CURSOR_TTL)

    structured_log(
        event="wechat_kf_welcome_synced",
        status="RECEIVED",
        extra={"open_kfid": kfid, "from_user": external_userid, "msg_id": msgid, "has_code": bool(welcome_code)},
    )
    return {
        "open_kf_id": kfid,
        "touser": external_userid,
        "welcome_code": welcome_code or None,
    }


def _route_synced_msg(redis: Redis, open_kfid: str, msg: dict) -> Optional[dict]:
    """处理 sync_msg 拉到的单条消息：仅「微信客户发送的文本」，做幂等去重 + 状态机分流。

    返回待回复任务参数 dict（session_id/open_id/open_kf_id/user_message/state/hit_keyword）；
    不满足条件（非 origin=3 / 非 text / 无内容 / 已处理过）返回 None。
    """
    if msg.get("origin") != 3 or msg.get("msgtype") != "text":
        return None
    content = (msg.get("text") or {}).get("content") or ""
    external_userid = msg.get("external_userid") or ""
    msgid = msg.get("msgid") or ""
    menu_id = (msg.get("text") or {}).get("menu_id") or ""  # 点击欢迎语菜单触发的消息会带
    if not content or not external_userid:
        return None

    # msgid 幂等去重：防游标丢失/并发重复拉取导致的重复回复
    dedup_key = f"wxkf:synced_msgid:{msgid}"
    if not redis.sadd(dedup_key, 1):
        return None
    redis.expire(dedup_key, SYNC_CURSOR_TTL)

    session_id = f"wxkf:{external_userid}"
    db = next(get_db())
    try:
        record = _restore_state_from_db(db, redis, session_id)
        result = sm.route_incoming(redis, session_id, content)
        _lazy_cleanup_expired(db)
        _persist_route_state(db, record, session_id, result, channel="WXKF", open_id=external_userid)
    finally:
        db.close()

    structured_log(
        event="wechat_kf_route",
        status=result["state"],
        extra={
            "session_id": session_id,
            "hit_keyword": result["hit_keyword"],
            "should_answer": result["should_answer"],
            "source": "sync_msg",
            "menu_id": menu_id or None,
        },
    )
    actual_kfid = msg.get("open_kfid") or open_kfid
    # 即时 ack：仅在需经由 Dify 思考的长链路问题时先推「收到」，问候语/菜单直出则不推冗余 ack
    if result["should_answer"] and not sm.is_pure_greeting(content) and not sm.is_menu_request(content):
        _try_push(actual_kfid, external_userid, settings.wxkf_ack_reply or _DEFAULT_WXKF_ACK_REPLY)
    return {
        "session_id": session_id,
        "open_id": external_userid,
        "open_kf_id": actual_kfid,
        "user_message": content,
        "state": result["state"],
        "hit_keyword": result["hit_keyword"],
    }


def _sync_and_collect(open_kfid: str, token: str) -> tuple[list[dict], list[dict]]:
    """同步：用 Token 调 sync_msg 增量拉取消息并逐条分流，返回 (待回复文本列表, 待推欢迎语列表)"""
    redis = get_redis()
    if not open_kfid and not token:
        structured_log(
            event="wxkf_sync_skipped",
            status="SKIPPED",
            extra={"reason": "neither open_kfid nor token provided"},
        )
        return [], []

    cursor_key = f"wxkf:sync_cursor:{open_kfid}" if open_kfid else "wxkf:sync_cursor:default"
    cursor = redis.get(cursor_key) or ""
    to_reply = []
    to_welcome = []

    # 分页：has_more=1 时继续，最多 5 页防异常死循环（正常一次即可拉完）
    for _ in range(5):
        try:
            data = wxkf_client.sync_msg(open_kfid, token=token, cursor=cursor)
        except WxKfError as exc:
            structured_log(
                event="wxkf_sync_failed",
                status="FAILED",
                error_msg=str(exc),
                extra={"open_kfid": open_kfid},
            )
            break

        for msg in data.get("msg_list") or []:
            msg_kfid = msg.get("open_kfid") or open_kfid
            welcome_item = _handle_synced_event(redis, msg_kfid, msg)
            if welcome_item:
                to_welcome.append(welcome_item)
                continue

            item = _route_synced_msg(redis, msg_kfid, msg)
            if item:
                to_reply.append(item)

        next_cursor = data.get("next_cursor") or ""
        if next_cursor:
            redis.set(cursor_key, next_cursor, ex=SYNC_CURSOR_TTL)
            cursor = next_cursor
        if not data.get("has_more"):
            break

    return to_reply, to_welcome


async def _sync_and_reply_async(open_kfid: str, token: str) -> None:
    """kf_msg_or_event 后台协程：sync_msg 网络/DB 逻辑放线程池，回复与欢迎语任务回事件循环调度"""
    try:
        to_reply, to_welcome = await asyncio.to_thread(_sync_and_collect, open_kfid, token)
    except Exception as exc:
        structured_log(
            event="wechat_kf_sync_task_error",
            status="FAILED",
            error_msg=str(exc),
            extra={"open_kfid": open_kfid},
        )
        return

    # 调度欢迎语推送（优先使用 welcome_code 调 send_msg_on_event）
    for w in to_welcome:
        asyncio.create_task(_push_welcome(w["open_kf_id"], w["touser"], w.get("welcome_code")))

    # 调度文本回复
    for item in to_reply:
        asyncio.create_task(_handle_wechat_reply(
            session_id=item["session_id"],
            open_id=item["open_id"],
            open_kf_id=item["open_kf_id"],
            user_message=item["user_message"],
            state=item["state"],
            hit_keyword=item["hit_keyword"],
        ))


