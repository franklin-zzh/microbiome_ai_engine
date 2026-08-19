"""企业微信群机器人 Webhook 客户端

支持向企微群主动推送：
1. 纯文本消息 (text，支持 @成员 / @all)
2. Markdown 消息 (markdown)
3. 模版卡片消息 (template_card，类型 text_notice / news_notice)

官方文档：https://developer.work.weixin.qq.com/document/path/99110
"""
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import structured_log


class WeComWebhookError(Exception):
    """企微 Webhook 调用异常"""


def _get_webhook_url(custom_url: Optional[str] = None) -> str:
    if custom_url:
        return custom_url
    url = get_settings().wecom_sales_webhook_url
    if not url:
        raise WeComWebhookError("WECOM_SALES_WEBHOOK_URL is not configured")
    return url


def send_webhook_raw(payload: Dict[str, Any], webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """发送原生 JSON Payload 至企业微信群 Webhook"""
    url = _get_webhook_url(webhook_url)
    try:
        resp = httpx.post(url, json=payload, timeout=10.0, trust_env=False)
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        structured_log(
            event="wecom_webhook_request_failed",
            status="FAILED",
            error_msg=str(exc),
            extra={"msgtype": payload.get("msgtype")},
        )
        raise WeComWebhookError(f"WeCom webhook request failed: {type(exc).__name__}") from exc

    errcode = data.get("errcode")
    if errcode != 0:
        structured_log(
            event="wecom_webhook_error_response",
            status="FAILED",
            error_msg=f"{errcode}: {data.get('errmsg')}",
            extra={"msgtype": payload.get("msgtype")},
        )
        raise WeComWebhookError(f"WeCom webhook returned errcode {errcode}: {data.get('errmsg')}")

    structured_log(
        event="wecom_webhook_sent",
        status="SUCCESS",
        extra={"msgtype": payload.get("msgtype")},
    )
    return data


def send_text(
    content: str,
    mentioned_list: Optional[List[str]] = None,
    mentioned_mobile_list: Optional[List[str]] = None,
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    """发送文本消息至群机器人 Webhook
    
    :param content: 文本内容，最长不超过2048个字节
    :param mentioned_list: userid 的列表，如 ["@all"] 或 ["zhangsan"]
    :param mentioned_mobile_list: 手机号列表，如 ["@all"] 或 ["13800001111"]
    """
    payload = {
        "msgtype": "text",
        "text": {
            "content": content,
            "mentioned_list": mentioned_list or [],
            "mentioned_mobile_list": mentioned_mobile_list or [],
        },
    }
    return send_webhook_raw(payload, webhook_url=webhook_url)


def send_markdown(content: str, webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """发送 Markdown 消息至群机器人 Webhook，最长不超过4096个字节"""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": content,
        },
    }
    return send_webhook_raw(payload, webhook_url=webhook_url)


def send_template_card(card_payload: Dict[str, Any], webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """发送模板卡片消息（template_card）"""
    payload = {
        "msgtype": "template_card",
        "template_card": card_payload,
    }
    return send_webhook_raw(payload, webhook_url=webhook_url)


def build_sales_intro_card(
    title: str = "富玛特招商合作与加盟政策",
    desc: str = "肠道微生态领军企业 · 诚邀城市合伙人与渠道分销商",
    emphasis_title: str = "1000亿+",
    emphasis_desc: str = "高活性益生菌活菌量 (CFU/袋)",
    quote_title: str = "企业背书与资质",
    quote_text: str = "国家高新技术企业 | GMP十万级无菌生产线 | 拥有一类/二类医疗器械及微生态专利矩阵",
    sub_title_text: str = "全方位商务支持与技术赋能，抢占千亿微生态健康蓝海！",
    horizontal_items: Optional[List[Dict[str, Any]]] = None,
    jump_items: Optional[List[Dict[str, Any]]] = None,
    action_url: str = "https://www.fmtcloud.cn",
) -> Dict[str, Any]:
    """构造标准【富玛特招商引介模板卡片】"""
    if horizontal_items is None:
        horizontal_items = [
            {"keyname": "招商顾问", "value": "小特 🤝"},
            {"keyname": "合作方向", "value": "城市独家代理 / 诊所药店渠道 / 医院科研合作"},
            {"keyname": "技术优势", "value": "精准定植技术 / 肠菌全周期干预方案"},
            {
                "keyname": "资质查阅",
                "value": "点击查看营业执照及资质",
                "type": 1,
                "url": action_url,
            },
        ]

    if jump_items is None:
        jump_items = [
            {"type": 1, "url": action_url, "title": "查看招商加盟手册"},
            {"type": 1, "url": action_url, "title": "申请合作与样品"},
        ]

    return {
        "card_type": "text_notice",
        "source": {
            "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0",
            "desc": "富玛特招商助手",
            "desc_color": 0,
        },
        "main_title": {
            "title": title,
            "desc": desc,
        },
        "emphasis_content": {
            "title": emphasis_title,
            "desc": emphasis_desc,
        },
        "quote_area": {
            "type": 1,
            "url": action_url,
            "title": quote_title,
            "quote_text": quote_text,
        },
        "sub_title_text": sub_title_text,
        "horizontal_content_list": horizontal_items,
        "jump_list": jump_items,
        "card_action": {
            "type": 1,
            "url": action_url,
        },
    }


def build_lead_alert_card(
    client_name: str,
    group_name: str,
    intent_summary: str,
    key_concerns: str = "商务价格 / 代理政策 / 签合同",
    confidence: float = 0.95,
    action_url: str = "https://www.fmtcloud.cn/admin",
) -> Dict[str, Any]:
    """构造标准【内部销售群商机线索通知卡片】"""
    return {
        "card_type": "text_notice",
        "source": {
            "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0",
            "desc": "富玛特商机雷达",
            "desc_color": 1,  # 红色/醒目
        },
        "main_title": {
            "title": "🚨 发现高意向招商线索 (已转人工)",
            "desc": f"群聊来源：{group_name}",
        },
        "emphasis_content": {
            "title": f"{int(confidence * 100)}%",
            "desc": "商机置信度",
        },
        "quote_area": {
            "type": 1,
            "url": action_url,
            "title": "客户诉求摘要",
            "quote_text": intent_summary,
        },
        "sub_title_text": "客户在外部群表达了明确合作/签约意愿，请销售专员尽快跟进！",
        "horizontal_content_list": [
            {"keyname": "客户身份", "value": client_name},
            {"keyname": "关注重点", "value": key_concerns},
            {"keyname": "处理状态", "value": "已暂停 AI 抢答，待人工介入"},
        ],
        "jump_list": [
            {"type": 1, "url": action_url, "title": "进入 CRM / 会话管理"},
        ],
        "card_action": {
            "type": 1,
            "url": action_url,
        },
    }
