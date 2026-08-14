"""dify_chat_client 测试：chat-messages（blocking）契约

- 请求 URL / headers / body 字段正确（query、user、response_mode=blocking）；
- conversation_id 只在多轮续聊时携带；
- 非 200 / 网络异常统一抛 DifyChatError（调用方兜底话术）。
"""
import httpx
from unittest.mock import patch

from app.clients import dify_chat_client
from app.clients.dify_chat_client import DifyChatError, chat_messages


def _settings(**overrides):
    base = {
        "dify_base_url": "http://dify/v1",
        "dify_chat_api_key": "",
        "dify_api_key": "kb-key",          # 未配 DIFY_CHAT_API_KEY 时回退
        "dify_chat_timeout_seconds": 120,
    }
    base.update(overrides)
    return type("S", (), base)()


def test_chat_messages_blocking_contract(monkeypatch):
    monkeypatch.setattr("app.clients.dify_chat_client._settings", lambda: _settings())
    with patch("httpx.post") as post:
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "answer": "你好",
            "conversation_id": "conv-1",
            "message_id": "msg-1",
            "metadata": {"retrieval_resources": [{"id": "seg-1", "score": 0.9}]},
        }
        result = chat_messages(query="检测报告怎么看", user="user-1")

        assert result["answer"] == "你好"
        assert result["conversation_id"] == "conv-1"
        assert result["retrieval_resources"] == [{"id": "seg-1", "score": 0.9}]

        assert post.call_args.args[0] == "http://dify/v1/chat-messages"
        kwargs = post.call_args.kwargs
        assert kwargs["headers"]["Authorization"] == "Bearer kb-key"  # 回退 DIFY_API_KEY
        assert kwargs["json"]["query"] == "检测报告怎么看"
        assert kwargs["json"]["response_mode"] == "blocking"
        assert kwargs["json"]["user"] == "user-1"
        assert "conversation_id" not in kwargs["json"]  # 新会话不带


def test_chat_messages_with_conversation_id_uses_chat_key(monkeypatch):
    monkeypatch.setattr("app.clients.dify_chat_client._settings", lambda: _settings(dify_chat_api_key="chat-key"))
    with patch("httpx.post") as post:
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"answer": "ok", "conversation_id": "conv-2"}
        chat_messages(query="再问一下", user="user-1", conversation_id="conv-2")
        kwargs = post.call_args.kwargs
        assert kwargs["json"]["conversation_id"] == "conv-2"
        assert kwargs["headers"]["Authorization"] == "Bearer chat-key"


def test_chat_messages_http_error_raises(monkeypatch):
    monkeypatch.setattr("app.clients.dify_chat_client._settings", lambda: _settings())
    with patch("httpx.post") as post:
        post.return_value.status_code = 500
        post.return_value.text = "internal error"
        try:
            chat_messages(query="q", user="u")
            raise AssertionError("expected DifyChatError")
        except DifyChatError as exc:
            assert "500" in str(exc)


def test_chat_messages_network_error_raises(monkeypatch):
    monkeypatch.setattr("app.clients.dify_chat_client._settings", lambda: _settings())
    with patch("httpx.post", side_effect=httpx.ConnectError("no route to host")):
        try:
            chat_messages(query="q", user="u")
            raise AssertionError("expected DifyChatError")
        except DifyChatError as exc:
            assert "request failed" in str(exc)
