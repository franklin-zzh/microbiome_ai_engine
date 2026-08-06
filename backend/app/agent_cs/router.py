"""客服 Agent 业务域：REST 路由

- chat_router：对话日志湖 / 会话状态机（mb_ai_engine.cs_*）
- wechat_router：企微「微信客服」/ 公众号回调入口（5 秒内必须回 success，AI 逻辑异步化）
"""
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
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
from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import structured_log
from app.core.redis import get_redis
from app.core.security import require_admin, require_internal_key
from app.clients.wx.wxbizmsgcrypt import WXBizMsgCrypt

chat_router = APIRouter(prefix="/chat", tags=["chat"])
wechat_router = APIRouter(tags=["wechat_kf"])
settings = get_settings()


# ============ 对话日志湖 ============


@chat_router.post("/logs", response_model=CsChatLogOut, dependencies=[Depends(require_internal_key)])
def create_cs_chat_log(
    body: CsChatLogCreate,
    db: Session = Depends(get_db),
):
    """写入一条全量对话日志（Data Lake 闭环入口）

    仅内部异步任务可写（X-API-Key，INTERNAL_API_KEY）。
    第 5 周 Task 5.1 之后由异步任务自动调用；当前先提供显式 API 便于联调。
    """
    record = CsChatLog(
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
    db.add(record)
    db.commit()
    db.refresh(record)

    structured_log(
        event="cs_chat_log_written",
        item_id=record.id,
        domain="CS",
        source_type="CHAT_LOG",
        status="WRITTEN",
        extra={"session_id": body.session_id, "channel": body.channel, "hit_human": body.hit_human},
    )
    return record


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

    # 持久化状态镜像（存在则更新，不存在则创建）；expires_at 滚动刷新（与 Redis TTL 对齐）
    expires_at = datetime.now() + timedelta(seconds=settings.session_ttl_seconds)
    if record is None:
        record = SessionState(
            session_id=session_id,
            channel=body.get("channel", "WXKF"),
            open_id=body.get("open_id", ""),
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
    完整链路（落库 + 异步调 Dify + 主动推送）按计划于 W3（Task 3.1/3.2）接入。
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
    try:
        root = ET.fromstring(raw)
        encrypt = root.findtext("Encrypt") or ""
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed XML body: {exc}")
    if not encrypt:
        raise HTTPException(status_code=400, detail="Missing Encrypt field")

    crypt = WXBizMsgCrypt(
        token=settings.wxkf_token,
        encoding_aes_key=settings.wxkf_encoding_aes_key,
        receive_id=settings.wxkf_corp_id,
    )
    try:
        # 验签失败抛 ValueError；解密后是内层明文 XML（含 FromUserName/Content 等）
        plain_xml = crypt.decrypt_msg(msg_signature, timestamp, nonce, encrypt)
        msg_root = ET.fromstring(plain_xml)
        from_user = msg_root.findtext("FromUserName") or ""
        msg_type = msg_root.findtext("MsgType") or ""
        content = msg_root.findtext("Content") or ""
        msg_id = msg_root.findtext("MsgId") or ""
    except Exception as exc:
        structured_log(
            event="wechat_kf_msg_verify_failed",
            status="FAILED",
            error_msg=str(exc),
            extra={"timestamp": timestamp},
        )
        raise HTTPException(status_code=400, detail=f"Callback verification failed: {exc}")

    # 只记录元数据（含 open_id 的消息原文待 W5 日志湖落库；日志不存原文，个保法）
    structured_log(
        event="wechat_kf_msg_received",
        status="RECEIVED",
        extra={
            "timestamp": timestamp,
            "from_user": from_user,
            "msg_type": msg_type,
            "content_len": len(content),
            "msg_id": msg_id,
        },
    )
    # 5 秒约束：先回 success，AI 逻辑异步化（W3 接入 Celery 链路）
    return Response(content="success", media_type="text/plain")
