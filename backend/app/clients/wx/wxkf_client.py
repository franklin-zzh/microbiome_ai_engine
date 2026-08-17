"""企业微信「微信客服」主动回复客户端

回调链路（接收用户消息 -> 后端处理 -> 主动推送回复）需要主动调用企微 API：
- ``gettoken``：corpid + secret 换 access_token（Redis 缓存，官方 7200s，提前刷新）
- ``kf/send_msg``：微信客服主动发消息（用户进入会话后可回复）
- ``kf/sync_msg``：同步拉取客服账号消息（``kf_msg_or_event`` 通知后调用，回调只带 Token/OpenKfId 不带消息体）

公众号（MP）推送走另一套 ``cgi-bin/message/custom/send``，后续接入时按渠道分发。
"""
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.logging import structured_log
from app.core.redis import get_redis

WXKF_API_BASE = "https://qyapi.weixin.qq.com"
ACCESS_TOKEN_TTL = 7000  # 官方 7200s，提前 200s 刷新
TOKEN_REDIS_KEY = "wx:kf:access_token"
# 这些 errcode 表示 access_token 失效/过期，清缓存重试一次
TOKEN_INVALID_ERRCODES = (40014, 42001, 4502)


class WxKfError(Exception):
    """企微微信客服 API 调用失败"""


def _settings():
    return get_settings()


def _fetch_access_token() -> str:
    settings = _settings()
    if not settings.wxkf_corp_id or not settings.wxkf_secret:
        raise WxKfError("WXKF_CORP_ID / WXKF_SECRET not configured in .env")
    try:
        resp = httpx.get(
            f"{WXKF_API_BASE}/cgi-bin/gettoken",
            params={"corpid": settings.wxkf_corp_id, "corpsecret": settings.wxkf_secret},
            timeout=10,
        )
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WxKfError(f"gettoken request failed: {type(exc).__name__}") from exc
    if data.get("errcode") != 0:
        raise WxKfError(f"gettoken failed: {data.get('errcode')} {data.get('errmsg')}")
    return data["access_token"]


def get_access_token(force_refresh: bool = False) -> str:
    """获取 access_token（Redis 缓存；force_refresh 用于失效后重取）"""
    redis = get_redis()
    if not force_refresh:
        cached = redis.get(TOKEN_REDIS_KEY)
        if cached:
            return cached
    token = _fetch_access_token()
    redis.set(TOKEN_REDIS_KEY, token, ex=ACCESS_TOKEN_TTL)
    return token


def send_kf_text(open_kf_id: str, touser: str, content: str) -> Optional[str]:
    """微信客服主动发送文本消息，返回 msgid（失败抛 WxKfError）。

    :param open_kf_id: 客服账号 open_kfid（回调消息里的 OpenKfId）；
    :param touser: 用户外部联系人 ID（回调消息里的 ExternalUserID）。
    """
    if not content:
        return None
    try:
        resp = httpx.post(
            f"{WXKF_API_BASE}/cgi-bin/kf/send_msg",
            params={"access_token": get_access_token()},
            json={"touser": touser, "msgtype": "text", "text": {"content": content}},
            timeout=15,
        )
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WxKfError(f"kf/send_msg request failed: {type(exc).__name__}") from exc

    # token 失效：清缓存重取重试一次
    if data.get("errcode") in TOKEN_INVALID_ERRCODES:
        try:
            resp = httpx.post(
                f"{WXKF_API_BASE}/cgi-bin/kf/send_msg",
                params={"access_token": get_access_token(force_refresh=True)},
                json={"touser": touser, "msgtype": "text", "text": {"content": content}},
                timeout=15,
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WxKfError(f"kf/send_msg retry failed: {type(exc).__name__}") from exc

    if data.get("errcode") != 0:
        raise WxKfError(f"kf/send_msg failed: {data.get('errcode')} {data.get('errmsg')}")

    structured_log(
        event="wxkf_send_msg",
        status="SENT",
        extra={"touser": touser, "msg_len": len(content), "msgid": data.get("msgid")},
    )
    return data.get("msgid")


def sync_msg(
    open_kfid: str,
    token: str = "",
    cursor: str = "",
    limit: int = 1000,
    voice_format: int = 0,
) -> dict:
    """同步拉取客服账号消息（``kf_msg_or_event`` 通知后调用），失败抛 WxKfError。

    :param open_kfid: 客服账号 open_kfid（回调里的 OpenKfId，必填）
    :param token: 回调事件里的 Token（10 分钟内有效；可不填，不填有严格频控）
    :param cursor: 上次返回的 next_cursor（增量拉取；首次为空则从 3 天内最早消息开始）
    :param limit: 单次条数上限（默认/最大 1000）
    :param voice_format: 0-Amr 1-Silk，默认 0
    :return: {"errcode":0, "next_cursor":..., "has_more":0/1, "msg_list":[...]}
    """
    body: dict = {"open_kfid": open_kfid, "limit": limit, "voice_format": voice_format}
    if token:
        body["token"] = token
    if cursor:
        body["cursor"] = cursor

    def _call(access_token: str) -> dict:
        resp = httpx.post(
            f"{WXKF_API_BASE}/cgi-bin/kf/sync_msg",
            params={"access_token": access_token},
            json=body,
            timeout=15,
        )
        return resp.json()

    try:
        data = _call(get_access_token())
    except (httpx.HTTPError, ValueError) as exc:
        raise WxKfError(f"kf/sync_msg request failed: {type(exc).__name__}") from exc

    # token 失效：清缓存重取重试一次
    if data.get("errcode") in TOKEN_INVALID_ERRCODES:
        try:
            data = _call(get_access_token(force_refresh=True))
        except (httpx.HTTPError, ValueError) as exc:
            raise WxKfError(f"kf/sync_msg retry failed: {type(exc).__name__}") from exc

    if data.get("errcode") != 0:
        raise WxKfError(f"kf/sync_msg failed: {data.get('errcode')} {data.get('errmsg')}")
    return data
