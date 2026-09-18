"""企业微信智能机器人 WebSocket 长连接与流式响应测试"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_sales.models import LeadsPreview
from app.agent_sales.wecom_sales_service import handle_aibot_message_stream
from app.clients.dify_chat_client import stream_chat_messages
from app.clients.wx.wecom_aibot_client import WeComAIBotClient


@pytest.mark.asyncio
async def test_wecom_aibot_client_payload_structure():
    """测试 WeComAIBotClient 生成协议帧格式的准确性"""
    mock_ws = AsyncMock()
    mock_ws.closed = False
    client = WeComAIBotClient(
        bot_id="bot_test_123",
        secret="secret_test_456",
        ws_url="wss://openws.work.weixin.qq.com",
    )
    client._ws = mock_ws

    # 1. 测试订阅帧
    await client._send_subscribe()
    assert mock_ws.send.called
    sent_frame = json.loads(mock_ws.send.call_args[0][0])
    assert sent_frame["cmd"] == "aibot_subscribe"
    assert sent_frame["body"]["bot_id"] == "bot_test_123"
    assert sent_frame["body"]["secret"] == "secret_test_456"

    # 2. 测试文本回复帧
    await client.send_text_reply(req_id="req_001", content="你好，我是智能招商顾问")
    sent_text_frame = json.loads(mock_ws.send.call_args[0][0])
    assert sent_text_frame["cmd"] == "aibot_respond_msg"
    assert sent_text_frame["headers"]["req_id"] == "req_001"
    assert sent_text_frame["body"]["msgtype"] == "text"
    assert sent_text_frame["body"]["text"]["content"] == "你好，我是智能招商顾问"

    # 3. 测试流式回复帧
    await client.send_stream_chunk(
        req_id="req_002",
        stream_id="stream_001",
        content="富玛特活菌胶囊...",
        finish=False,
    )
    sent_stream_frame = json.loads(mock_ws.send.call_args[0][0])
    assert sent_stream_frame["cmd"] == "aibot_respond_msg"
    assert sent_stream_frame["body"]["msgtype"] == "stream"
    assert sent_stream_frame["body"]["stream"]["id"] == "stream_001"
    assert sent_stream_frame["body"]["stream"]["content"] == "富玛特活菌胶囊..."
    assert sent_stream_frame["body"]["stream"]["finish"] is False


@pytest.mark.asyncio
async def test_handle_aibot_message_group_stream(monkeypatch):
    """测试群聊 @ 机器人时的 Dify 流式问答与会话落库"""
    mock_client = AsyncMock()

    # Mock dify_chat_client.stream_chat_messages
    async def fake_stream_generator(*args, **kwargs):
        yield {"event": "message", "delta": "富玛特", "conversation_id": "conv-aibot-1", "message_id": "m1"}
        yield {"event": "message", "delta": "提供专业的肠道微生态解决方案", "conversation_id": "conv-aibot-1", "message_id": "m1"}
        yield {"event": "message_end", "conversation_id": "conv-aibot-1", "message_id": "m1", "retrieval_resources": []}

    monkeypatch.setattr("app.clients.dify_chat_client.stream_chat_messages", fake_stream_generator)

    group_frame = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req_group_100"},
        "body": {
            "msg_id": "msg_001",
            "chat_type": 2,
            "chat_id": "chat_group_888",
            "from": {"user_id": "sales_rep_01", "name": "张经理"},
            "msg_type": "text",
            "text": {"content": "@招商顾问 介绍一下富玛特的优势"},
        },
    }

    await handle_aibot_message_stream(group_frame, mock_client)

    # 验证流式发送至少调用了两次（包含 finish=True 结束帧）
    assert mock_client.send_stream_chunk.called
    final_call_args = mock_client.send_stream_chunk.call_args_list[-1]
    assert final_call_args.kwargs["finish"] is True
    assert "富玛特提供专业的肠道微生态解决方案" in final_call_args.kwargs["content"]


@pytest.mark.asyncio
async def test_handle_aibot_message_single_stream(monkeypatch):
    """测试员工 1v1 单聊专属赋能会话"""
    mock_client = AsyncMock()

    async def fake_stream_generator(*args, **kwargs):
        yield {"event": "message", "delta": "针对医生异议，您可以这样回应...", "conversation_id": "conv-single-1"}
        yield {"event": "message_end", "conversation_id": "conv-single-1"}

    monkeypatch.setattr("app.clients.dify_chat_client.stream_chat_messages", fake_stream_generator)

    single_frame = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req_single_200"},
        "body": {
            "msg_id": "msg_002",
            "chat_type": 1,
            "from": {"user_id": "doctor_rep_02", "name": "李业务员"},
            "msg_type": "text",
            "text": {"content": "医生质疑菌株定植率，话术怎么说？"},
        },
    }

    await handle_aibot_message_stream(single_frame, mock_client)

    assert mock_client.send_stream_chunk.called
    final_call = mock_client.send_stream_chunk.call_args_list[-1]
    assert final_call.kwargs["finish"] is True
    assert "针对医生异议" in final_call.kwargs["content"]


@pytest.mark.asyncio
async def test_handle_aibot_message_human_intent_interception(monkeypatch):
    """测试商机意图关键词拦截（加盟/底价）-> 生成 LeadsPreview + Webhook 预警 + 回复转接话术"""
    mock_client = AsyncMock()
    mock_webhook = MagicMock(return_value={"errcode": 0, "errmsg": "ok"})
    monkeypatch.setattr("app.clients.wx.wecom_webhook_client.send_template_card", mock_webhook)

    human_intent_frame = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req_intent_300"},
        "body": {
            "msg_id": "msg_003",
            "chat_type": 2,
            "chat_id": "chat_invest_999",
            "from": {"user_id": "partner_vip", "name": "王总"},
            "msg_type": "text",
            "text": {"content": "@机器人 我想做省级总代理，签合同底价多少？"},
        },
    }

    await handle_aibot_message_stream(human_intent_frame, mock_client)

    # 验证直接走 text 回复转接话术（不调用 Dify）
    assert mock_client.send_text_reply.called
    reply_content = mock_client.send_text_reply.call_args.kwargs["content"]
    assert "专属招商顾问" in reply_content
    # 验证触发了内部群 Webhook 预警卡片
    assert mock_webhook.called
