"""企业微信「自建应用」API 客户端

用于接收到外部群/内部群消息回调后，主动调用企微应用 API 发送回复消息。
- ``gettoken``: corpid + corpsecret 获取 access_token（Redis 缓存 7000s）
- ``message/send``: 向指定用户发送应用消息
- ``appchat/send``: 向指定群聊发送群消息
"""
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import structured_log
from app.core.redis import get_redis

WECOM_API_BASE = "https://qyapi.weixin.qq.com"
ACCESS_TOKEN_TTL = 7000
TOKEN_REDIS_KEY = "wx:sales_app:access_token"
TOKEN_INVALID_ERRCODES = (40014, 42001, 4502)


class WeComAppError(Exception):
    """企微自建应用 API 调用异常"""


_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            timeout=15.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=60.0),
        )
    return _client


def _fetch_access_token() -> str:
    settings = get_settings()
    corp_id = settings.wecom_sales_corp_id or settings.wxkf_corp_id
    secret = settings.wecom_sales_secret or settings.wxkf_secret
    if not corp_id or not secret:
        raise WeComAppError("WECOM_SALES_CORP_ID / WECOM_SALES_SECRET not configured in .env")

    try:
        resp = _get_client().get(
            f"{WECOM_API_BASE}/cgi-bin/gettoken",
            params={"corpid": corp_id, "corpsecret": secret},
            timeout=10.0,
        )
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WeComAppError(f"gettoken request failed: {type(exc).__name__}") from exc

    if data.get("errcode") != 0:
        raise WeComAppError(f"gettoken failed: {data.get('errcode')} {data.get('errmsg')}")

    return data["access_token"]


def get_access_token(force_refresh: bool = False) -> str:
    """获取自建应用的 access_token（带 Redis 缓存）"""
    redis = get_redis()
    if not force_refresh:
        cached = redis.get(TOKEN_REDIS_KEY)
        if cached:
            return cached
    token = _fetch_access_token()
    redis.set(TOKEN_REDIS_KEY, token, ex=ACCESS_TOKEN_TTL)
    return token


def _post_with_token(endpoint: str, body: dict) -> dict:
    """带 access_token 的 POST 请求（支持 token 过期自动重试一次）"""
    def _call(token: str) -> dict:
        resp = _get_client().post(
            f"{WECOM_API_BASE}/{endpoint.lstrip('/')}",
            params={"access_token": token},
            json=body,
            timeout=15.0,
        )
        return resp.json()

    try:
        data = _call(get_access_token())
    except (httpx.HTTPError, ValueError) as exc:
        raise WeComAppError(f"{endpoint} request failed: {type(exc).__name__}") from exc

    if data.get("errcode") in TOKEN_INVALID_ERRCODES:
        try:
            data = _call(get_access_token(force_refresh=True))
        except (httpx.HTTPError, ValueError) as exc:
            raise WeComAppError(f"{endpoint} retry failed: {type(exc).__name__}") from exc

    if data.get("errcode") != 0:
        raise WeComAppError(f"{endpoint} failed: {data.get('errcode')} {data.get('errmsg')}")

    return data


def send_appchat_text(chatid: str, content: str) -> Dict[str, Any]:
    """向企业微信群聊发送应用消息 (appchat/send)"""
    body = {
        "chatid": chatid,
        "msgtype": "text",
        "text": {"content": content},
        "safe": 0,
    }
    data = _post_with_token("cgi-bin/appchat/send", body)
    structured_log(
        event="wecom_appchat_text_sent",
        status="SENT",
        extra={"chatid": chatid, "content_len": len(content)},
    )
    return data


def send_appchat_markdown(chatid: str, content: str) -> Dict[str, Any]:
    """向企业微信群聊发送 Markdown 应用消息 (appchat/send)"""
    body = {
        "chatid": chatid,
        "msgtype": "markdown",
        "markdown": {"content": content},
    }
    data = _post_with_token("cgi-bin/appchat/send", body)
    structured_log(
        event="wecom_appchat_markdown_sent",
        status="SENT",
        extra={"chatid": chatid, "content_len": len(content)},
    )
    return data
