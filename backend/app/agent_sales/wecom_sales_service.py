"""销售 Agent 业务域：企微招商群消息处理与业务编排

职责：
1. 解析并清洗群聊 `@机器人` 提问；
2. 状态机与意图分流：
   - 商务谈判 / 签约合作 / 价格折扣 / 转人工 -> 触发人工接管、沉淀线索至 cs_leads_preview、向内部销售群 Webhook 发送商机卡片；
   - 招商政策 / 技术参数（活菌量） / 产品优势 / 资质执照 -> 调用 Dify 招商工作流获取回答并回传至群聊；
3. 全量记录对话日志湖（cs_chat_logs，channel=WECOM_GROUP）。
"""
import re
from typing import Any, Dict, List, Optional, Tuple

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
                user_message=clean_query,
                ai_reply=SALES_DIFY_FALLBACK_REPLY,
                hit_human=False,
                meta={"route": "dify_sales_error", "chat_id": chat_id},
            ))
            db.commit()
            return SALES_DIFY_FALLBACK_REPLY, "FALLBACK"

    finally:
        db.close()
