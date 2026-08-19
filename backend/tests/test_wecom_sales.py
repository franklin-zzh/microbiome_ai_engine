"""企业微信招商 Agent 与 Webhook 路由集成测试"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.agent_sales.models import LeadsPreview
from app.agent_sales.wecom_sales_service import (
    clean_mention_text,
    is_sales_human_intent,
    process_sales_group_message,
)
from app.core.config import get_settings
from main import app

settings = get_settings()
engine = create_engine(settings.test_database_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
client = TestClient(app)


def test_clean_mention_text():
    assert clean_mention_text("@小特 活菌量是多少？") == "活菌量是多少？"
    assert clean_mention_text("@富玛特招商顾问 加盟政策如何？") == "加盟政策如何？"
    assert clean_mention_text("直接提问") == "直接提问"


def test_is_sales_human_intent():
    assert is_sales_human_intent("我想签合同，底价是多少？") is True
    assert is_sales_human_intent("请问怎么转人工客服？") is True
    assert is_sales_human_intent("有代理费返点政策吗？") is True
    assert is_sales_human_intent("你们的活菌量是多少？有检测报告吗？") is False


def test_process_sales_group_message_dify_flow(monkeypatch):
    test_session_id = "wecom_group:chat123:user456"
    with patch("app.clients.dify_chat_client.chat_messages") as mock_dify:
        mock_dify.return_value = {
            "answer": "富玛特益生菌单袋活菌量超过1000亿CFU，采用定向定植技术。",
            "conversation_id": "conv-test-1",
        }
        reply, action = process_sales_group_message(
            chat_id="chat123",
            from_user="user456",
            raw_content="@小特 活菌量是多少？",
        )
        assert action == "AI_REPLY"
        assert "1000亿CFU" in reply


def test_process_sales_group_message_human_handoff(monkeypatch):
    test_session_id = "wecom_group:chat123:user789"
    with patch("app.clients.wx.wecom_webhook_client.send_template_card") as mock_card:
        mock_card.return_value = {"errcode": 0, "errmsg": "ok"}
        reply, action = process_sales_group_message(
            chat_id="chat123",
            from_user="user789",
            raw_content="@小特 我要签独家代理合同，谈一下底价",
        )
        assert action == "HUMAN_HANDOFF"
        assert "专属招商顾问" in reply
        assert mock_card.called


def test_api_push_sales_intro_card():
    with patch("app.clients.wx.wecom_webhook_client.send_template_card") as mock_card:
        mock_card.return_value = {"errcode": 0, "errmsg": "ok"}
        resp = client.post(
            "/api/v1/sales/wecom/push-card",
            json={
                "title": "富玛特全国招商峰会",
                "desc": "千亿级肠道微生态蓝海市场",
                "emphasis_title": "1000亿+",
                "emphasis_desc": "高活性活菌量",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "富玛特全国招商峰会" in data["card_summary"]["title"]
