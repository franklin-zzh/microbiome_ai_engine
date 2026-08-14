"""wxkf_client 测试：企微微信客服 access_token 缓存 / 主动推送 / token 失效重试

网络层全部 mock；Redis 用本地实例（与 test_session_state 一致），测试后清理 key。
"""
from unittest.mock import patch

from app.clients.wx.wxkf_client import (
    TOKEN_REDIS_KEY,
    WxKfError,
    get_access_token,
    send_kf_text,
)
from app.core.redis import get_redis


def _settings(corp="c1", secret="s1"):
    return type("S", (), {"wxkf_corp_id": corp, "wxkf_secret": secret})()


def _cleanup():
    get_redis().delete(TOKEN_REDIS_KEY)


def test_get_access_token_caches_in_redis(monkeypatch):
    _cleanup()
    monkeypatch.setattr("app.clients.wx.wxkf_client._settings", lambda: _settings())
    with patch("httpx.get") as get:
        get.return_value.json.return_value = {"errcode": 0, "access_token": "tok-1"}
        assert get_access_token() == "tok-1"
        assert get.call_count == 1
        # 第二次命中 Redis 缓存，不再请求企微
        assert get_access_token() == "tok-1"
        assert get.call_count == 1
        # force_refresh 强制重取
        get.return_value.json.return_value = {"errcode": 0, "access_token": "tok-2"}
        assert get_access_token(force_refresh=True) == "tok-2"
        assert get.call_count == 2
    _cleanup()


def test_get_access_token_missing_config_raises(monkeypatch):
    monkeypatch.setattr("app.clients.wx.wxkf_client._settings", lambda: _settings(corp="", secret=""))
    try:
        get_access_token(force_refresh=True)
        raise AssertionError("expected WxKfError")
    except WxKfError as exc:
        assert "not configured" in str(exc)


def test_get_access_token_errcode_raises(monkeypatch):
    monkeypatch.setattr("app.clients.wx.wxkf_client._settings", lambda: _settings())
    with patch("httpx.get") as get:
        get.return_value.json.return_value = {"errcode": 40013, "errmsg": "invalid corp id"}
        try:
            get_access_token(force_refresh=True)
            raise AssertionError("expected WxKfError")
        except WxKfError as exc:
            assert "40013" in str(exc)


def test_send_kf_text_ok(monkeypatch):
    _cleanup()
    monkeypatch.setattr("app.clients.wx.wxkf_client._settings", lambda: _settings())
    monkeypatch.setattr("app.clients.wx.wxkf_client.get_access_token", lambda force_refresh=False: "tok-2")
    with patch("httpx.post") as post:
        post.return_value.json.return_value = {"errcode": 0, "msgid": "m1"}
        msgid = send_kf_text("kf-1", "user-1", "你好")
        assert msgid == "m1"
        body = post.call_args.kwargs["json"]
        assert body["touser"] == "user-1"
        assert body["msgtype"] == "text"
        assert body["text"]["content"] == "你好"
        assert post.call_args.kwargs["params"]["access_token"] == "tok-2"
    _cleanup()


def test_send_kf_text_token_invalid_retries_once(monkeypatch):
    _cleanup()
    monkeypatch.setattr("app.clients.wx.wxkf_client._settings", lambda: _settings())
    monkeypatch.setattr("app.clients.wx.wxkf_client.get_access_token", lambda force_refresh=False: "tok-new")
    with patch("httpx.post") as post:
        post.return_value.json.side_effect = [
            {"errcode": 42001, "errmsg": "token expired"},
            {"errcode": 0, "msgid": "m2"},
        ]
        assert send_kf_text("kf-1", "user-1", "hi") == "m2"
        assert post.call_count == 2
    _cleanup()


def test_send_kf_text_business_error_raises(monkeypatch):
    _cleanup()
    monkeypatch.setattr("app.clients.wx.wxkf_client._settings", lambda: _settings())
    monkeypatch.setattr("app.clients.wx.wxkf_client.get_access_token", lambda force_refresh=False: "tok-3")
    with patch("httpx.post") as post:
        post.return_value.json.return_value = {"errcode": 6000, "errmsg": "boom"}
        try:
            send_kf_text("kf-1", "user-1", "hi")
            raise AssertionError("expected WxKfError")
        except WxKfError as exc:
            assert "6000" in str(exc)
    _cleanup()
