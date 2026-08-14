"""Dify 客服对话客户端（chat-messages）

职责：企微/公众号等外部渠道的消息经后端转发，调用 Dify 应用的
``POST /chat-messages``（blocking 模式）拿到完整回答后回传渠道。

与 ``dify_knowledge_client.py`` 的分工：
- 知识库客户端：Dataset / Pipeline 写入端（后端是事实源）；
- 本客户端：客服对话（advanced-chat 应用）只读调用，App API Key 单独配置
  （``DIFY_CHAT_API_KEY``，空值期间回退 ``DIFY_API_KEY`` 平滑迁移）。

契约见 docs/DIFY-API-CONTRACT.md（Dify 1.16.x，DSL 版本 0.7.0）。
"""
from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings


class DifyChatError(Exception):
    """Dify 对话调用失败（网络/超时/非 2xx/业务错误），调用方应兜底回复用户"""


def _settings():
    return get_settings()


def _api_key() -> str:
    settings = _settings()
    key = settings.dify_chat_api_key or settings.dify_api_key
    if not key:
        raise DifyChatError("DIFY_CHAT_API_KEY is not configured")
    return key


def _base_url() -> str:
    return _settings().dify_base_url.rstrip("/")


def chat_messages(
    query: str,
    user: str,
    conversation_id: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """调用 Dify ``chat-messages``（blocking），返回 {answer, conversation_id, message_id, retrieval_resources}。

    :param user: 渠道用户标识（企微 open_id / 公众号 openid），Dify 按 user 隔离会话；
    :param conversation_id: 已有会话 ID（多轮续聊）；None = 新会话；
    :param inputs: 工作流起始节点变量（本应用为空即可）。
    """
    settings = _settings()
    body = {
        "inputs": inputs or {},
        "query": query,
        "response_mode": "blocking",
        "user": user,
    }
    if conversation_id:
        body["conversation_id"] = conversation_id

    try:
        resp = httpx.post(
            f"{_base_url()}/chat-messages",
            headers={"Authorization": f"Bearer {_api_key()}"},
            json=body,
            timeout=timeout or settings.dify_chat_timeout_seconds,
            trust_env=False,  # 与 dify_knowledge_client 一致：禁用系统代理，避免本机代理导致的 502/超时
        )
    except httpx.HTTPError as exc:
        raise DifyChatError(f"dify chat-messages request failed: {type(exc).__name__}") from exc

    if resp.status_code != 200:
        raise DifyChatError(f"dify chat-messages returned {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    # blocking 模式下必含 answer；conversation_id 用于多轮续聊
    return {
        "answer": data.get("answer", ""),
        "conversation_id": data.get("conversation_id"),
        "message_id": data.get("message_id"),
        # 知识库召回资源（若有），供对话日志湖记录检索来源
        "retrieval_resources": (data.get("metadata") or {}).get("retrieval_resources") or [],
    }
