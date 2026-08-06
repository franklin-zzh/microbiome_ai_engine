"""认证路由：POST /api/v1/auth/login -> JWT

单 admin 账号凭据来自 .env（ADMIN_USERNAME / ADMIN_PASSWORD），
密码比较使用常量时间算法（hmac.compare_digest）防时序侧信道。
"""
import hmac

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import structured_log
from app.core.security import ROLE_ADMIN, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    ok = hmac.compare_digest(body.username, settings.admin_username) and hmac.compare_digest(
        body.password, settings.admin_password
    )
    if not ok:
        structured_log(
            event="auth_login_failed",
            status="FAILED",
            extra={"username": body.username},
        )
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token(body.username, ROLE_ADMIN)
    structured_log(
        event="auth_login_success",
        status="SUCCESS",
        extra={"username": body.username},
    )
    return LoginResponse(access_token=token, role=ROLE_ADMIN)
