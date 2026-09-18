"""企业微信智能机器人（API 模式）WebSocket 长连接客户端

协议基于企业微信 OpenWS 长连接规范：
- WebSocket 接入点：wss://openws.work.weixin.qq.com
- 身份认证与订阅指令：``aibot_subscribe``
- 消息回调接收指令：``aibot_msg_callback``
- 消息回复指令：``aibot_respond_msg``（支持 text / markdown / stream 流式打字机）
- 保活心跳：定时 ping 帧
"""
import asyncio
import json
import uuid
from typing import Any, Callable, Coroutine, Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import get_settings
from app.core.logging import structured_log

WECOM_OPENWS_DEFAULT_URL = "wss://openws.work.weixin.qq.com"


class WeComAIBotError(Exception):
    """企微智能机器人客户端异常"""


class WeComAIBotClient:
    """企业微信智能机器人 WebSocket 长连接管理器"""

    def __init__(
        self,
        bot_id: Optional[str] = None,
        secret: Optional[str] = None,
        ws_url: Optional[str] = None,
        on_message: Optional[Callable[[Dict[str, Any], "WeComAIBotClient"], Coroutine[Any, Any, None]]] = None,
    ):
        settings = get_settings()
        self.bot_id = bot_id or settings.wecom_aibot_id
        self.secret = secret or settings.wecom_aibot_secret
        self.ws_url = ws_url or settings.wecom_aibot_ws_url or WECOM_OPENWS_DEFAULT_URL
        self.on_message = on_message

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running: bool = False
        self._main_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        # 1. 优先检查 websockets 13+/14+/16+ (state.name == "OPEN")
        state = getattr(self._ws, "state", None)
        if state is not None and hasattr(state, "name") and isinstance(state.name, str):
            return state.name == "OPEN"
        # 2. 兼容早期版本 websockets 与 Mock 对象的 .closed / .open 属性
        if hasattr(self._ws, "closed"):
            try:
                return not bool(self._ws.closed)
            except (AttributeError, TypeError):
                pass
        if hasattr(self._ws, "open"):
            try:
                return bool(self._ws.open)
            except (AttributeError, TypeError):
                pass
        return True

    async def start(self):
        """启动后台长连接 Worker（非阻塞）"""
        if self._running:
            return
        if not self.bot_id or not self.secret:
            structured_log(
                event="wecom_aibot_skipped",
                status="SKIPPED",
                extra={"reason": "WECOM_AIBOT_ID or WECOM_AIBOT_SECRET not configured"},
            )
            return

        self._running = True
        self._main_task = asyncio.create_task(self._run_loop(), name="wecom_aibot_worker")
        structured_log(
            event="wecom_aibot_worker_started",
            status="RUNNING",
            extra={"bot_id": self.bot_id, "ws_url": self.ws_url},
        )

    async def stop(self):
        """停止长连接 Worker 并优雅关闭连接"""
        self._running = False
        if self._ws is not None and self.is_connected:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        structured_log(event="wecom_aibot_worker_stopped", status="STOPPED")

    async def _run_loop(self):
        """主循环：维护 WebSocket 连接、订阅与断线指数退避重连"""
        retry_delay = 1.0
        max_retry_delay = 30.0

        while self._running:
            try:
                structured_log(
                    event="wecom_aibot_connecting",
                    status="CONNECTING",
                    extra={"ws_url": self.ws_url},
                )
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=None,  # 企微 OpenWS 不响应 RFC 6455 Ping 帧，禁用以防客户端误判超时断开
                    ping_timeout=None,
                    close_timeout=10,
                ) as ws:
                    self._ws = ws
                    retry_delay = 1.0  # 连接成功重置退避延迟

                    # 1. 发送 aibot_subscribe 认证帧
                    subscribed = await self._send_subscribe()
                    if not subscribed:
                        structured_log(
                            event="wecom_aibot_subscribe_failed",
                            status="FAILED",
                            error_msg="Failed to subscribe bot credentials",
                        )
                        await asyncio.sleep(5.0)
                        continue

                    structured_log(
                        event="wecom_aibot_connected",
                        status="CONNECTED",
                        extra={"bot_id": self.bot_id},
                    )

                    # 2. 消费接收消息帧
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        asyncio.create_task(self._handle_raw_frame(raw_msg))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._running:
                    break
                err_text = str(exc) or repr(exc)
                structured_log(
                    event="wecom_aibot_connection_error",
                    status="RECONNECTING",
                    error_msg=err_text,
                    extra={"retry_in_seconds": retry_delay},
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)

    async def _send_subscribe(self) -> bool:
        """向企微服务端发送 aibot_subscribe 鉴权帧"""
        req_id = str(uuid.uuid4())
        subscribe_frame = {
            "cmd": "aibot_subscribe",
            "headers": {"req_id": req_id},
            "body": {
                "bot_id": self.bot_id,
                "secret": self.secret,
            },
        }
        await self._send_json(subscribe_frame)
        return True

    async def _handle_raw_frame(self, raw_frame: str):
        """解析企微推送的 JSON 报文并分发"""
        try:
            data = json.loads(raw_frame)
        except Exception as exc:
            structured_log(
                event="wecom_aibot_parse_error",
                status="FAILED",
                error_msg=str(exc),
                extra={"raw": str(raw_frame)[:200]},
            )
            return

        cmd = data.get("cmd")
        headers = data.get("headers") or {}
        req_id = headers.get("req_id")
        body = data.get("body") or {}

        if cmd == "aibot_subscribe":
            errcode = data.get("errcode", 0)
            errmsg = data.get("errmsg", "ok")
            structured_log(
                event="wecom_aibot_subscribe_ack",
                status="SUCCESS" if errcode == 0 else "FAILED",
                extra={"errcode": errcode, "errmsg": errmsg, "req_id": req_id},
            )
            return

        if cmd == "aibot_msg_callback":
            msgtype = body.get("msgtype") or body.get("msg_type")
            chat_id = body.get("chatid") or body.get("chat_id")
            chat_type = body.get("chattype") or body.get("chat_type")
            from_user = (body.get("from") or {}).get("userid") or (body.get("from") or {}).get("user_id") if isinstance(body.get("from"), dict) else body.get("from")
            structured_log(
                event="wecom_aibot_msg_received",
                status="RECEIVED",
                extra={
                    "req_id": req_id,
                    "chat_id": chat_id,
                    "chat_type": chat_type,
                    "from_user": from_user,
                    "msg_type": msgtype,
                    "raw_body": body,
                },
            )
            if self.on_message:
                try:
                    await self.on_message(data, self)
                except Exception as exc:
                    structured_log(
                        event="wecom_aibot_handler_failed",
                        status="FAILED",
                        error_msg=str(exc),
                        extra={"req_id": req_id},
                    )

    async def _send_json(self, payload: Dict[str, Any]):
        """线程/并发安全的 WebSocket JSON 发送"""
        if not self.is_connected:
            raise WeComAIBotError("WebSocket is not connected")
        async with self._send_lock:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def send_text_reply(self, req_id: str, content: str):
        """一次性发送纯文本回复"""
        payload = {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {
                "msgtype": "text",
                "text": {"content": content},
            },
        }
        await self._send_json(payload)

    async def send_markdown_reply(self, req_id: str, content: str):
        """一次性发送 Markdown 回复"""
        payload = {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {
                "msgtype": "markdown",
                "markdown": {"content": content},
            },
        }
        await self._send_json(payload)

    async def send_stream_chunk(
        self,
        req_id: str,
        stream_id: str,
        content: str,
        finish: bool = False,
    ):
        """发送流式打字机消息切片

        :param req_id: 企微原始请求的 req_id
        :param stream_id: 该流式消息唯一的 stream_id
        :param content: 累计/当前内容
        :param finish: 是否为最后一段流式结束帧
        """
        payload = {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "finish": finish,
                    "content": content,
                },
            },
        }
        await self._send_json(payload)


# 单例客户端
_default_bot_client: Optional[WeComAIBotClient] = None


def get_wecom_aibot_client() -> WeComAIBotClient:
    global _default_bot_client
    if _default_bot_client is None:
        from app.agent_sales.wecom_sales_service import handle_aibot_message_stream
        _default_bot_client = WeComAIBotClient(on_message=handle_aibot_message_stream)
    return _default_bot_client
