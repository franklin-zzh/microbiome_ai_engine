"""轻量 JWT 鉴权（P0 安全基线）

三层鉴权（见 docs/ARCHITECTURE-REVIEW.md「三、改进方案」）：
  1. 人类用户（Admin / 医生 / 销售）  -> JWT（/api/v1/auth/login 签发），依赖 require_admin
  2. Dify Workflow 工具回调           -> Header X-API-Key（INTERNAL_API_KEY），依赖 require_internal_key
  3. 微信公众平台 Webhook             -> 官方 Token 签名验证（WXBizMsgCrypt，见 app/agent_cs/router.py）

角色模型当前只有 admin 单账号（.env 配置）；多角色（医生/销售）为 R5 演进，
payload 已带 role 字段，扩展时只需增加角色常量与对应依赖。
"""
import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

settings = get_settings()

ALGORITHM = "HS256"
ROLE_ADMIN = "admin"

_bearer = HTTPBearer(auto_error=False)
_internal_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def create_access_token(username: str, role: str = ROLE_ADMIN, expire_minutes: Optional[int] = None) -> str:
    """签发 JWT（HS256）。exp 默认取 settings.jwt_expire_minutes。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes or settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """校验并解析 JWT；无效/过期抛 jwt.PyJWTError。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])


def get_current_user(cred: Optional[HTTPAuthorizationCredentials] = Security(_bearer)) -> dict:
    """任意已登录用户（Bearer token -> {"username", "role"}）"""
    if cred is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(cred.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}")
    return {"username": payload.get("sub", ""), "role": payload.get("role", "")}


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """仅 Admin 角色可访问（知识审核 / 对话日志 / 销售线索等敏感接口）"""
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privilege required")
    return user


def require_internal_key(key: Optional[str] = Security(_internal_key_header)) -> None:
    """内部共享密钥（Dify workflow 回调 / 异步链路），Header: X-API-Key"""
    if not key or not hmac.compare_digest(key, settings.internal_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal API key")
