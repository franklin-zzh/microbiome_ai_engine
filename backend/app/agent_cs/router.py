"""客服 Agent 业务域：REST 路由

- chat_router：对话日志湖 / 会话状态机（mb_ai_cs）
- wechat_router：企微「微信客服」/ 公众号回调入口（5 秒内必须回 success，AI 逻辑异步化）
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from redis import Redis
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
from app.core.database import get_db_cs
from app.core.logging import structured_log
from app.core.redis import get_redis
from app.core.wxbizmsgcrypt import WXBizMsgCrypt

chat_router = APIRouter(prefix="/chat", tags=["chat"])
wechat_router = APIRouter(tags=["wechat_kf"])
settings = get_settings()


# ============ 对话日志湖 ============


@chat_router.post("/logs", response_model=CsChatLogOut)
def create_cs_chat_log(
    body: CsChatLogCreate,
    db: Session = Depends(get_db_cs),
):
    """写入一条全量对话日志（Data Lake 闭环入口）

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


@chat_router.get("/logs", response_model=CsChatLogListResponse)
def list_cs_chat_logs(
    session_id: str = Query(None),
    channel: str = Query(None, pattern="^(WXKF|MP|H5|WECOM_GROUP)$"),
    hit_human: bool = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db_cs),
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


@chat_router.get("/session/{session_id}", response_model=SessionStateOut)
def get_session_state(
    session_id: str,
    db: Session = Depends(get_db_cs),
    redis: Redis = Depends(get_redis),
):
    """读取会话状态（Redis 优先，DB 兜底重建）"""
    state = sm.get_state(redis, session_id)
    record = db.query(SessionState).filter(SessionState.session_id == session_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
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


@chat_router.post("/session/{session_id}/route")
def route_message(
    session_id: str,
    body: dict,
    db: Session = Depends(get_db_cs),
    redis: Redis = Depends(get_redis),
):
    """入站消息路由（状态机分流）——微信回调异步链路的入口判断

    body: {"user_message": "...", "channel": "WXKF", "open_id": "..."}
    返回该消息的处理路径；消息本体应随后进入队列异步调 Dify。
    """
    user_message = body.get("user_message", "")
    if not user_message:
        raise HTTPException(status_code=422, detail="user_message is required")

    result = sm.route_incoming(redis, session_id, user_message)

    # 持久化状态镜像（存在则更新，不存在则创建）
    record = db.query(SessionState).filter(SessionState.session_id == session_id).first()
    if record is None:
        record = SessionState(
            session_id=session_id,
            channel=body.get("channel", "WXKF"),
            open_id=body.get("open_id", ""),
            state=SessionStateValue(result["state"]),
        )
        db.add(record)
    else:
        record.state = SessionStateValue(result["state"])
        if result["state"] == sm.STATE_NORMAL:
            record.negative_streak = 0
    db.commit()

    return {
        "session_id": session_id,
        "route": result,
        "action": "pass_to_human" if not result["should_answer"] else "queue_for_ai",
    }


@chat_router.post("/session/{session_id}/release")
def release_session(
    session_id: str,
    db: Session = Depends(get_db_cs),
    redis: Redis = Depends(get_redis),
):
    """人工接管完成 / 管理员重置会话状态 -> NORMAL"""
    sm.release_human(redis, session_id)
    record = db.query(SessionState).filter(SessionState.session_id == session_id).first()
    if record:
        record.reset()
        db.commit()
    return {"session_id": session_id, "state": sm.STATE_NORMAL}


# ============ 企微微信客服 / 公众号回调 ============


class WechatCallbackPostBody(BaseModel):
    ToUserName: str = ""
    Encrypt: str = ""
    AgentID: str = ""


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
        from fastapi import Response

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
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """企微客服 接收用户消息回调 (POST 请求)"""
    # 先向企微回应 success (5秒内必须响应)
    structured_log(
        event="wechat_kf_msg_received",
        status="RECEIVED",
        extra={"timestamp": timestamp},
    )
    from fastapi import Response

    return Response(content="success", media_type="text/plain")
