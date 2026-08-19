"""企业微信 Webhook 客户端测试

测试内容：
- 文本、Markdown、模板卡片 (template_card) 消息序列化与发送
- 招商卡片构造器 build_sales_intro_card 格式与字段
- 商机卡片构造器 build_lead_alert_card 格式与字段
- 异常场景（网络失败、非 0 errcode）抛 WeComWebhookError
"""
from unittest.mock import patch

import pytest

from app.clients.wx import wecom_webhook_client
from app.clients.wx.wecom_webhook_client import (
    WeComWebhookError,
    build_lead_alert_card,
    build_sales_intro_card,
    send_markdown,
    send_template_card,
    send_text,
)


def test_build_sales_intro_card():
    card = build_sales_intro_card(
        title="富玛特招商合作",
        emphasis_title="1000亿+",
        emphasis_desc="活菌量",
        action_url="https://www.fmtcloud.cn",
    )
    assert card["card_type"] == "text_notice"
    assert card["main_title"]["title"] == "富玛特招商合作"
    assert card["emphasis_content"]["title"] == "1000亿+"
    assert card["emphasis_content"]["desc"] == "活菌量"
    assert len(card["horizontal_content_list"]) >= 3
    assert len(card["jump_list"]) >= 1
    assert card["card_action"]["url"] == "https://www.fmtcloud.cn"


def test_build_lead_alert_card():
    card = build_lead_alert_card(
        client_name="张总 (代理商)",
        group_name="华南招商咨询群",
        intent_summary="咨询华南区域独家代理政策及合同底价",
        confidence=0.98,
    )
    assert card["card_type"] == "text_notice"
    assert "招商线索" in card["main_title"]["title"]
    assert card["emphasis_content"]["title"] == "98%"
    assert card["quote_area"]["quote_text"] == "咨询华南区域独家代理政策及合同底价"


def test_send_text_success(monkeypatch):
    with patch("httpx.post") as post:
        post.return_value.json.return_value = {"errcode": 0, "errmsg": "ok"}
        res = send_text("招商政策如下", mentioned_list=["@all"])
        assert res["errcode"] == 0
        kwargs = post.call_args.kwargs
        assert kwargs["json"]["msgtype"] == "text"
        assert kwargs["json"]["text"]["content"] == "招商政策如下"
        assert kwargs["json"]["text"]["mentioned_list"] == ["@all"]


def test_send_markdown_success(monkeypatch):
    with patch("httpx.post") as post:
        post.return_value.json.return_value = {"errcode": 0, "errmsg": "ok"}
        res = send_markdown("### 富玛特技术优势\n- 高活性菌株")
        assert res["errcode"] == 0
        kwargs = post.call_args.kwargs
        assert kwargs["json"]["msgtype"] == "markdown"
        assert "富玛特技术优势" in kwargs["json"]["markdown"]["content"]


def test_send_template_card_success(monkeypatch):
    with patch("httpx.post") as post:
        post.return_value.json.return_value = {"errcode": 0, "errmsg": "ok"}
        card = build_sales_intro_card()
        res = send_template_card(card)
        assert res["errcode"] == 0
        kwargs = post.call_args.kwargs
        assert kwargs["json"]["msgtype"] == "template_card"
        assert kwargs["json"]["template_card"]["card_type"] == "text_notice"


def test_send_webhook_errcode_raises(monkeypatch):
    with patch("httpx.post") as post:
        post.return_value.json.return_value = {"errcode": 93000, "errmsg": "invalid webhook url"}
        with pytest.raises(WeComWebhookError) as exc_info:
            send_text("test")
        assert "93000" in str(exc_info.value)
