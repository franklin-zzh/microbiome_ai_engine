import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import get_db
from app.knowledge.models import KnowledgeItem, KnowledgeStatus, UnansweredQuestion, UnansweredStatus
from main import app

settings = get_settings()
# conftest 已把 DATABASE_URL 指向 mb_ai_engine_test；此处直接取测试库连接串
engine = create_engine(settings.test_database_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
# 后台任务（如审核通过后的 Dify 同步）自建 session 时也需指向测试库：
# services.sync_approved_knowledge 在 db=None 时使用模块级 SessionLocal（生产库），
# 测试中将其替换为 TestingSessionLocal，否则后台任务查不到测试库里的 item
import app.knowledge.services as knowledge_services

knowledge_services.SessionLocal = TestingSessionLocal
client = TestClient(app)

# 表结构由 conftest.migrated_test_db 通过 `alembic upgrade head` 建好，
# 不再需要 setup_module 里的手工 DROP + create_all。


def test_submit_knowledge():
    response = client.post("/api/v1/knowledge/submit", json={
        "domain": "CS",
        "title": "测试 FAQ",
        "question": "测试问题",
        "answer": "测试答案",
        "tags": ["测试"],
        "source_type": "MANUAL",
        "created_by": "pytest",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PENDING"
    assert data["domain"] == "CS"


def test_approve_knowledge_triggers_sync_mock(monkeypatch):
    captured = {}

    def fake_sync(*args, **kwargs):
        captured["called"] = True
        captured["args"] = kwargs
        return "fake-doc-id"

    import app.knowledge.services as knowledge_services
    monkeypatch.setattr(knowledge_services, "sync_knowledge_to_dify", fake_sync)

    submit = client.post("/api/v1/knowledge/submit", json={
        "domain": "CS",
        "title": "待审核 FAQ",
        "question": "问题",
        "answer": "答案",
    })
    item_id = submit.json()["id"]

    response = client.post(f"/api/v1/admin/knowledge/{item_id}/approve", json={"approved_by": "admin"})
    assert response.status_code == 200
    assert response.json()["message"] == "Approved and sync scheduled"
    assert captured.get("called") is True


def test_capture_unanswered():
    response = client.post("/api/v1/cs/unanswered/capture", json={
        "user_query": "测试未解答问题",
        "match_score": 0.45,
        "context": {"channel": "dify"},
    })
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Unanswered question captured"


def test_list_unanswered():
    response = client.get("/api/v1/cs/unanswered?status=OPEN")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_list_knowledge():
    response = client.get("/api/v1/admin/knowledge?domain=CS&status=PENDING")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1


def test_submit_with_category_and_filter():
    # 提交时显式指定主分类
    submit = client.post("/api/v1/knowledge/submit", json={
        "domain": "CS",
        "title": "分类测试 FAQ",
        "question": "分类测试问题",
        "answer": "分类测试答案",
        "tags": ["测试"],
        "source_type": "MANUAL",
        "created_by": "pytest",
        "category": "product.probiotics",
    })
    assert submit.status_code == 200
    assert submit.json()["category"] == "product.probiotics"

    # 未指定 category 时兜底为 GENERAL（test_submit_knowledge 提交的条目）
    resp = client.get("/api/v1/admin/knowledge?category=GENERAL")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1

    # 按 category 精确筛选
    resp = client.get("/api/v1/admin/knowledge?category=product.probiotics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert all(i["category"] == "product.probiotics" for i in data["items"])
