"""P0 鉴权测试：登录签发 JWT、未授权 401、内部密钥校验

- 受保护接口（admin/chat/logs/session 等）无 token 必须 401；
- 错误密码登录必须 401；
- Dify 回调（/cs/unanswered/capture）无/错 X-API-Key 必须 401。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import get_db
from main import app

settings = get_settings()
engine = create_engine(settings.test_database_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def test_admin_endpoints_reject_anonymous():
    """无 token 访问敏感接口（知识文档审核 / 对话日志 / 销售线索）一律 401"""
    for url in (
        "/api/v1/admin/knowledge/documents",
        "/api/v1/chat/logs",
        "/api/v1/chat/leads",
        "/api/v1/cs/unanswered",
    ):
        assert client.get(url).status_code == 401, url


def test_login_failure_and_success():
    # 错误密码 -> 401
    r = client.post("/api/v1/auth/login", json={"username": "testadmin", "password": "wrong-password"})
    assert r.status_code == 401

    # 正确凭据 -> JWT
    r = client.post("/api/v1/auth/login", json={"username": "testadmin", "password": "testpass"})
    assert r.status_code == 200
    data = r.json()
    assert data["role"] == "admin"
    assert data["token_type"] == "bearer"

    # 带 token 后放行
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    assert client.get("/api/v1/admin/knowledge/documents", headers=headers).status_code == 200


def test_invalid_token_rejected():
    r = client.get("/api/v1/admin/knowledge/documents", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_capture_requires_internal_key():
    # 无 key / 错 key -> 401
    assert client.post("/api/v1/cs/unanswered/capture", json={"user_query": "x"}).status_code == 401
    assert (
        client.post(
            "/api/v1/cs/unanswered/capture",
            json={"user_query": "x"},
            headers={"X-API-Key": "wrong-key"},
        ).status_code
        == 401
    )
    # 正确 key -> 200（Dify workflow 回调路径）
    r = client.post(
        "/api/v1/cs/unanswered/capture",
        json={"user_query": "鉴权测试问题", "match_score": 0.4},
        headers={"X-API-Key": settings.internal_api_key},
    )
    assert r.status_code == 200


def test_route_requires_internal_key():
    """异步链路入口 /chat/session/{id}/route 仅内部密钥可调"""
    r = client.post("/api/v1/chat/session/unauth-session/route", json={"user_message": "hi"})
    assert r.status_code == 401
