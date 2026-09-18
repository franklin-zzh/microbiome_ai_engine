"""销售 Agent 业务域：企微招商群消息处理与业务编排

职责：
1. 解析并清洗群聊 `@机器人` 提问；
2. 状态机与意图分流：
   - 商务谈判 / 签约合作 / 价格折扣 / 转人工 -> 触发人工接管、沉淀线索至 cs_leads_preview、向内部销售群 Webhook 发送商机卡片；
   - 招商政策 / 技术参数（活菌量） / 产品优势 / 资质执照 -> 调用 Dify 招商工作流获取回答并回传至群聊；
3. 全量记录对话日志湖（cs_chat_logs，channel=WECOM_GROUP）。
"""
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy.orm import Session

from app.agent_cs import services as sm
from app.agent_cs.models import ChatChannel, CsChatLog, SessionState, SessionStateValue
from app.agent_sales.models import LeadStatus, LeadsPreview
from app.clients import dify_chat_client
from app.clients.dify_chat_client import DifyChatError
from app.clients.wx import wecom_app_client, wecom_webhook_client
from app.clients.wx.wecom_webhook_client import WeComWebhookError
from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import structured_log
from app.core.redis import get_redis

settings = get_settings()

# 招商商机/人工协同高频触发关键词
SALES_HUMAN_KEYWORDS = [
    "签合同", "签约", "合同条款", "合作协议",
    "底价", "最低价", "代理费", "加盟费", "返点", "折扣政策",
    "转人工", "人工客服", "人工顾问", "招商经理", "找销售",
    "量大优惠", "采购意向", "做代理", "独家代理",
]

SALES_HUMAN_HANDOFF_REPLY = (
    "收到您的合作意向！已为您转接富玛特专属招商顾问，稍后销售经理将在群内或私聊与您详细对接商务政策与合同细节 🤝"
)
SALES_DIFY_FALLBACK_REPLY = (
    "您好，小特当前正在查询最新的招商与产品技术资料，您也可以直接在群内留言或联系我们的招商专员～"
)


def is_sales_human_intent(message: str) -> bool:
    """判断提问是否命中招商人工转接 / 高价值商机意图"""
    msg = message.strip()
    return any(kw in msg for kw in SALES_HUMAN_KEYWORDS)


def clean_mention_text(content: str) -> str:
    """去除群聊消息中的 @ 机器人前缀与 XML 标记"""
    # 去除类似 @小特 或 @xxx 的前缀
    cleaned = re.sub(r"@[\w\u4e00-\u9fa5]+\s*", "", content).strip()
    return cleaned or content.strip()


def create_sales_lead(
    db: Session,
    session_id: str,
    open_id: str,
    user_message: str,
    channel: ChatChannel = ChatChannel.WECOM_GROUP,
    confidence: float = 0.95,
    group_name: str = "企微招商群",
) -> LeadsPreview:
    """在 cs_leads_preview 中沉淀一条高意向销售线索，并向内部销售群 Webhook 发送商机卡片"""
    lead = LeadsPreview(
        session_id=session_id,
        open_id=open_id,
        channel=channel,
        intent_tags={"intent": "PARTNERSHIP_INQUIRY", "source": "WECOM_GROUP_MENTION"},
        summary=f"客户在群 [{group_name}] 中咨询商务合作：{user_message[:100]}",
        confidence=confidence,
        status=LeadStatus.NEW,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    structured_log(
        event="sales_lead_created",
        item_id=lead.id,
        domain="SALES",
        status="NEW",
        extra={"session_id": session_id, "open_id": open_id},
    )

    # 异步/即时通过 Webhook 推送商机卡片至内部销售群
    try:
        card = wecom_webhook_client.build_lead_alert_card(
            client_name=open_id,
            group_name=group_name,
            intent_summary=user_message,
            confidence=confidence,
        )
        wecom_webhook_client.send_template_card(card)
    except Exception as exc:
        structured_log(
            event="sales_lead_webhook_card_failed",
            status="FAILED",
            error_msg=str(exc),
            extra={"lead_id": lead.id},
        )

    return lead


def process_sales_group_message(
    chat_id: str,
    from_user: str,
    raw_content: str,
    group_name: str = "企业微信外部群",
) -> Tuple[str, str]:
    """处理外部群消息核心编排流程
    
    返回 (reply_text, action_type)
    """
    clean_query = clean_mention_text(raw_content)
    session_id = f"wecom_group:{chat_id}:{from_user}"

    db = next(get_db())
    redis = get_redis()

    try:
        # 1. 检查状态机与意图
        state = sm.get_state(redis, session_id)

        # 命中人工接管意图
        if is_sales_human_intent(clean_query) or state == sm.STATE_HUMAN_MODE:
            sm.set_state(redis, session_id, sm.STATE_HUMAN_MODE)
            # 沉淀商机线索
            create_sales_lead(
                db=db,
                session_id=session_id,
                open_id=from_user,
                user_message=clean_query,
                group_name=group_name,
            )
            # 记录日志湖
            db.add(CsChatLog(
                channel=ChatChannel.WECOM_GROUP,
                session_id=session_id,
                open_id=from_user,
                chat_type="group",
                chat_id=chat_id,
                user_message=clean_query,
                ai_reply=SALES_HUMAN_HANDOFF_REPLY,
                hit_human=True,
                human_takeover=True,
                meta={"route": "sales_human_handoff", "chat_id": chat_id},
            ))
            db.commit()
            return SALES_HUMAN_HANDOFF_REPLY, "HUMAN_HANDOFF"

        # 2. 常规咨询，调用 Dify 招商 Agent
        conversation_id = sm.get_dify_conversation(redis, session_id)
        dify_api_key = settings.dify_sales_chat_api_key or settings.dify_chat_api_key

        try:
            dify_res = dify_chat_client.chat_messages(
                query=clean_query,
                user=from_user,
                conversation_id=conversation_id,
                api_key=dify_api_key,
            )
            answer = dify_res.get("answer") or SALES_DIFY_FALLBACK_REPLY
            if dify_res.get("conversation_id"):
                sm.set_dify_conversation(redis, session_id, dify_res["conversation_id"])

            db.add(CsChatLog(
                channel=ChatChannel.WECOM_GROUP,
                session_id=session_id,
                open_id=from_user,
                chat_type="group",
                chat_id=chat_id,
                user_message=clean_query,
                ai_reply=answer,
                retrieved_chunks=dify_res.get("retrieval_resources") or None,
                meta={"route": "dify_sales", "chat_id": chat_id, "conversation_id": dify_res.get("conversation_id")},
            ))
            db.commit()
            return answer, "AI_REPLY"

        except DifyChatError as exc:
            structured_log(
                event="dify_sales_chat_failed",
                status="FAILED",
                error_msg=str(exc),
                extra={"session_id": session_id},
            )
            db.add(CsChatLog(
                channel=ChatChannel.WECOM_GROUP,
                session_id=session_id,
                open_id=from_user,
                chat_type="group",
                chat_id=chat_id,
                user_message=clean_query,
                ai_reply=SALES_DIFY_FALLBACK_REPLY,
                hit_human=False,
                meta={"route": "dify_sales_error", "chat_id": chat_id},
            ))
            db.commit()
            return SALES_DIFY_FALLBACK_REPLY, "FALLBACK"

    finally:
        db.close()


async def handle_aibot_message_stream(frame_data: Dict[str, Any], client: Any):
    """处理企业微信智能机器人 WebSocket 长连接推送的消息事件 (aibot_msg_callback)

    同时支持：
    1. 内部群聊 @ 机器人（带 chat_id，会话 Key 为 wecom_group:{chat_id}:{user_id}）
    2. 员工 1v1 单聊（无 chat_id，会话 Key 为 wecom_single:{user_id}）
    """
    headers = frame_data.get("headers") or {}
    req_id = headers.get("req_id")
    if not req_id:
        return

    body = frame_data.get("body") or {}
    msg_type = body.get("msgtype") or body.get("msg_type") or ("text" if "text" in body else "")
    if msg_type != "text":
        structured_log(
            event="wecom_aibot_unsupported_msg_type",
            status="IGNORED",
            extra={"msg_type": msg_type, "req_id": req_id, "raw_body": body},
        )
        return

    if isinstance(body.get("text"), dict):
        raw_content = body["text"].get("content", "")
    elif isinstance(body.get("text"), str):
        raw_content = body["text"]
    else:
        raw_content = body.get("content", "")

    from_obj = body.get("from") or {}
    if isinstance(from_obj, dict):
        from_user = str(from_obj.get("userid") or from_obj.get("user_id") or "").strip()
        user_name = from_obj.get("name") or from_user
    elif isinstance(from_obj, str):
        from_user = from_obj.strip()
        user_name = from_user
    else:
        from_user = str(body.get("from_user_id") or body.get("from_user") or "").strip()
        user_name = from_user

    chat_id = body.get("chatid") or body.get("chat_id")
    raw_chat_type = str(body.get("chattype") or body.get("chat_type") or "").lower()
    is_group = bool(chat_id or raw_chat_type in ("group", "2"))

    if not from_user:
        msg_id = body.get("msgid") or req_id or uuid.uuid4().hex[:8]
        from_user = f"member_{msg_id[:8]}"
        user_name = "群成员"

    clean_query = clean_mention_text(raw_content)
    if not clean_query:
        return

    # 会话隔离 Key
    if is_group:
        group_key = chat_id or "default_group"
        session_id = f"wecom_group:{group_key}:{from_user}"
        channel = ChatChannel.WECOM_GROUP
        group_name = "企微招商群"
    else:
        session_id = f"wecom_single:{from_user}"
        channel = ChatChannel.WECOM_GROUP
        group_name = "企微1v1单聊"

    db = next(get_db())
    redis = get_redis()

    try:
        # 1. 意图拦截与人工转接
        state = sm.get_state(redis, session_id)
        if is_sales_human_intent(clean_query) or state == sm.STATE_HUMAN_MODE:
            sm.set_state(redis, session_id, sm.STATE_HUMAN_MODE)
            create_sales_lead(
                db=db,
                session_id=session_id,
                open_id=from_user,
                user_message=clean_query,
                channel=channel,
                group_name=group_name,
            )
            db.add(CsChatLog(
                channel=channel,
                session_id=session_id,
                open_id=from_user,
                chat_type="group" if is_group else "single",
                chat_id=chat_id,
                user_message=clean_query,
                ai_reply=SALES_HUMAN_HANDOFF_REPLY,
                hit_human=True,
                human_takeover=True,
                meta={"route": "sales_human_handoff_aibot", "chat_type": "group" if is_group else "single", "chat_id": chat_id, "is_group": is_group},
            ))
            db.commit()
            await client.send_text_reply(req_id=req_id, content=SALES_HUMAN_HANDOFF_REPLY)
            return

        # 2. Dify 招商/商务/销售流式生成（群聊优先调 partner_agent，私聊优先调 sales_agent）
        conversation_id = sm.get_dify_conversation(redis, session_id)
        if is_group:
            dify_api_key = settings.dify_partner_chat_api_key or settings.dify_sales_chat_api_key or settings.dify_chat_api_key
        else:
            dify_api_key = settings.dify_sales_chat_api_key or settings.dify_partner_chat_api_key or settings.dify_chat_api_key
        stream_id = f"stream_{uuid.uuid4().hex[:16]}"

        full_text = ""
        last_sent_text = ""
        last_send_time = time.time()
        retrieval_resources = []
        new_conversation_id = conversation_id

        try:
            async for chunk in dify_chat_client.stream_chat_messages(
                query=clean_query,
                user=from_user,
                conversation_id=conversation_id,
                api_key=dify_api_key,
            ):
                event = chunk.get("event")
                if event == "message":
                    delta = chunk.get("delta", "")
                    full_text += delta
                    if chunk.get("conversation_id"):
                        new_conversation_id = chunk["conversation_id"]

                    now = time.time()
                    # 缓冲节流：每 150ms 或累积新字符数达到 20 时发送一次中间切片
                    if (now - last_send_time >= 0.15 or len(full_text) - len(last_sent_text) >= 20) and full_text != last_sent_text:
                        await client.send_stream_chunk(
                            req_id=req_id,
                            stream_id=stream_id,
                            content=full_text,
                            finish=False,
                        )
                        last_sent_text = full_text
                        last_send_time = now

                elif event == "message_end":
                    if chunk.get("conversation_id"):
                        new_conversation_id = chunk["conversation_id"]
                    retrieval_resources = chunk.get("retrieval_resources") or []

            # 发送最后一段带有 finish=True 的结束帧
            final_reply = full_text.strip() or SALES_DIFY_FALLBACK_REPLY
            await client.send_stream_chunk(
                req_id=req_id,
                stream_id=stream_id,
                content=final_reply,
                finish=True,
            )

            # 更新会话 ID 与落库日志
            if new_conversation_id:
                sm.set_dify_conversation(redis, session_id, new_conversation_id)

            db.add(CsChatLog(
                channel=channel,
                session_id=session_id,
                open_id=from_user,
                chat_type="group" if is_group else "single",
                chat_id=chat_id,
                user_message=clean_query,
                ai_reply=final_reply,
                retrieved_chunks=retrieval_resources or None,
                meta={
                    "route": "dify_sales_aibot_stream",
                    "chat_type": "group" if is_group else "single",
                    "chat_id": chat_id,
                    "is_group": is_group,
                    "stream_id": stream_id,
                    "conversation_id": new_conversation_id,
                },
            ))
            db.commit()

        except Exception as exc:
            structured_log(
                event="wecom_aibot_stream_error",
                status="FAILED",
                error_msg=str(exc),
                extra={"session_id": session_id, "req_id": req_id},
            )
            # 异常降级兜底
            fallback_text = (full_text + "\n" + SALES_DIFY_FALLBACK_REPLY) if full_text else SALES_DIFY_FALLBACK_REPLY
            try:
                await client.send_stream_chunk(
                    req_id=req_id,
                    stream_id=stream_id,
                    content=fallback_text,
                    finish=True,
                )
            except Exception:
                await client.send_text_reply(req_id=req_id, content=SALES_DIFY_FALLBACK_REPLY)

            db.add(CsChatLog(
                channel=channel,
                session_id=session_id,
                open_id=from_user,
                chat_type="group" if is_group else "single",
                chat_id=chat_id,
                user_message=clean_query,
                ai_reply=fallback_text,
                hit_human=False,
                meta={
                    "route": "dify_sales_aibot_error",
                    "chat_type": "group" if is_group else "single",
                    "chat_id": chat_id,
                    "is_group": is_group,
                },
            ))
            db.commit()

    finally:
        db.close()

