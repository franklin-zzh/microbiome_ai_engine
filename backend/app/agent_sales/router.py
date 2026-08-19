"""销售 Agent 业务域：REST 路由

1. 企业微信自建应用 / 招商群消息回调：
   - GET /sales/wecom/callback：企微配置回调 URL 校验
   - POST /sales/wecom/callback：接收外部群 @ 机器人提问并异步回复
2. 企业微信群 Webhook 消息推送：
   - POST /sales/wecom/push-card：推送招商引介模板卡片（template_card）
   - POST /sales/wecom/push-lead：推送高意向商机线索通知卡片
   - POST /sales/wecom/push-raw：通用文本/Markdown/卡片推送
3. 销售线索管理：
   - GET /chat/leads：获取线索列表
"""
import asyncio
import xml.etree.ElementTree as ET
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.agent_sales import wecom_sales_service as sales_service
from app.agent_sales.models import LeadsPreview
from app.agent_sales.schemas import (
    LeadsPreviewOut,
    WeComLeadAlertPushRequest,
    WeComSalesIntroPushRequest,
    WeComWebhookPushRequest,
)
from app.clients.wx import wecom_app_client, wecom_webhook_client
from app.clients.wx.wecom_webhook_client import WeComWebhookError
from app.clients.wx.wxbizmsgcrypt import WXBizMsgCrypt
from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import structured_log
from app.core.security import require_admin

router = APIRouter(prefix="", tags=["sales"])
settings = get_settings()


# ==================== 1. 企业微信自建应用回调 (群聊 @ 机器人) ====================


def _get_crypt() -> WXBizMsgCrypt:
    token = settings.wecom_sales_token or settings.wxkf_token
    aes_key = settings.wecom_sales_encoding_aes_key or settings.wxkf_encoding_aes_key
    corp_id = settings.wecom_sales_corp_id or settings.wxkf_corp_id
    if not token or not aes_key:
        raise HTTPException(
            status_code=500,
            detail="WECOM_SALES_TOKEN / WECOM_SALES_ENCODING_AES_KEY not configured in backend",
        )
    return WXBizMsgCrypt(token=token, encoding_aes_key=aes_key, receive_id=corp_id)


@router.get("/sales/wecom/callback")
@router.get("/wx/sales/msg")
def verify_sales_wecom_url(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """企微自建应用 URL 校验回调 (GET 请求)"""
    try:
        crypt = _get_crypt()
        reply_echo = crypt.decrypt_echo_str(
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
        )
        structured_log(
            event="wecom_sales_verify_success",
            status="SUCCESS",
            extra={"reply_echo": reply_echo},
        )
        return Response(content=reply_echo, media_type="text/plain")
    except Exception as exc:
        structured_log(
            event="wecom_sales_verify_error",
            status="FAILED",
            error_msg=str(exc),
        )
        raise HTTPException(status_code=400, detail=f"URL Verification Failed: {exc}")


async def _reply_group_async(chat_id: str, from_user: str, raw_content: str, group_name: str):
    """后台异步执行 Dify 招商思考与群内回复"""
    try:
        reply_text, action = await asyncio.to_thread(
            sales_service.process_sales_group_message,
            chat_id=chat_id,
            from_user=from_user,
            raw_content=raw_content,
            group_name=group_name,
        )

        formatted_reply = f"@{from_user}\n{reply_text}"

        # 优先通过群机器人 Webhook 发送 @ 消息，也可以调用 appchat API
        try:
            wecom_webhook_client.send_text(
                content=formatted_reply,
                mentioned_list=[from_user],
            )
        except Exception:
            if chat_id:
                wecom_app_client.send_appchat_text(chat_id=chat_id, content=formatted_reply)

        structured_log(
            event="wecom_sales_group_replied",
            status="SENT",
            extra={"chat_id": chat_id, "from_user": from_user, "action": action},
        )
    except Exception as exc:
        structured_log(
            event="wecom_sales_group_reply_task_failed",
            status="FAILED",
            error_msg=str(exc),
            extra={"chat_id": chat_id, "from_user": from_user},
        )


@router.post("/sales/wecom/callback")
@router.post("/wx/sales/msg")
async def handle_sales_wecom_message(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """企微自建应用 / 外部群接收消息回调 (POST 请求)
    
    5 秒内必须返回 success，AI 思考与回复后台异步执行。
    """
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

    try:
        crypt = _get_crypt()
        plain_xml = crypt.decrypt_msg(msg_signature, timestamp, nonce, encrypt)
        msg_root = ET.fromstring(plain_xml)
        msg_type = msg_root.findtext("MsgType") or ""
        from_user = msg_root.findtext("FromUserName") or ""
        content = msg_root.findtext("Content") or ""
        chat_id = msg_root.findtext("ChatId") or ""
        msg_id = msg_root.findtext("MsgId") or ""
    except Exception as exc:
        structured_log(
            event="wecom_sales_msg_decrypt_failed",
            status="FAILED",
            error_msg=str(exc),
        )
        raise HTTPException(status_code=400, detail=f"Callback decrypt failed: {exc}")

    structured_log(
        event="wecom_sales_msg_received",
        status="RECEIVED",
        extra={
            "from_user": from_user,
            "chat_id": chat_id,
            "msg_type": msg_type,
            "msg_id": msg_id,
            "content_len": len(content),
        },
    )

    # 仅处理文本消息
    if msg_type == "text" and content:
        asyncio.create_task(
            _reply_group_async(
                chat_id=chat_id,
                from_user=from_user,
                raw_content=content,
                group_name=f"群聊_{chat_id[:8]}" if chat_id else "招商客户群",
            )
        )

    return Response(content="success", media_type="text/plain")


# ==================== 2. 企业微信群 Webhook 消息主动推送 ====================


@router.post("/sales/wecom/push-raw", dependencies=[Depends(require_admin)])
def push_wecom_webhook_raw(req: WeComWebhookPushRequest):
    """通用 Webhook 消息推送（支持 text, markdown, template_card）"""
    try:
        if req.msgtype == "template_card" and req.template_card:
            res = wecom_webhook_client.send_template_card(req.template_card, webhook_url=req.webhook_url)
        elif req.msgtype == "markdown" and req.content:
            res = wecom_webhook_client.send_markdown(req.content, webhook_url=req.webhook_url)
        elif req.content:
            res = wecom_webhook_client.send_text(
                content=req.content,
                mentioned_list=req.mentioned_list,
                webhook_url=req.webhook_url,
            )
        else:
            raise HTTPException(status_code=422, detail="Missing message content or template_card")
        return {"status": "ok", "wecom_response": res}
    except WeComWebhookError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/sales/wecom/push-card")
def push_sales_intro_card(req: WeComSalesIntroPushRequest):
    """主动向企微群推送【富玛特招商合作与技术优势模板卡片 (template_card)】"""
    try:
        card = wecom_webhook_client.build_sales_intro_card(
            title=req.title,
            desc=req.desc,
            emphasis_title=req.emphasis_title,
            emphasis_desc=req.emphasis_desc,
            quote_text=req.quote_text,
            action_url=req.action_url,
        )
        res = wecom_webhook_client.send_template_card(card, webhook_url=req.webhook_url)
        return {
            "status": "ok",
            "msg": "富玛特招商模板卡片推送成功",
            "card_summary": {
                "title": req.title,
                "emphasis": f"{req.emphasis_title} ({req.emphasis_desc})",
            },
            "wecom_response": res,
        }
    except WeComWebhookError as exc:
        raise HTTPException(status_code=502, detail=f"Webhook 推送失败: {exc}")


@router.post("/sales/wecom/push-lead", dependencies=[Depends(require_admin)])
def push_lead_alert_card(req: WeComLeadAlertPushRequest):
    """向销售群主动推送【高意向商机线索通知卡片】"""
    try:
        card = wecom_webhook_client.build_lead_alert_card(
            client_name=req.client_name,
            group_name=req.group_name,
            intent_summary=req.intent_summary,
            key_concerns=req.key_concerns,
            confidence=req.confidence,
        )
        res = wecom_webhook_client.send_template_card(card, webhook_url=req.webhook_url)
        return {
            "status": "ok",
            "msg": "商机线索卡片推送成功",
            "wecom_response": res,
        }
    except WeComWebhookError as exc:
        raise HTTPException(status_code=502, detail=f"Webhook 推送失败: {exc}")


# ==================== 3. 销售线索列表 ====================


@router.get("/chat/leads", response_model=list[LeadsPreviewOut], dependencies=[Depends(require_admin)])
def list_leads(
    status: str = Query(None, pattern="^(NEW|ASSIGNED|CONVERTED|CLOSED)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(LeadsPreview)
    if status:
        query = query.filter(LeadsPreview.status == status)
    return query.order_by(LeadsPreview.created_at.desc()).offset(skip).limit(limit).all()
