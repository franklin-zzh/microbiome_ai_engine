import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.knowledge.models import KnowledgeItem, KnowledgeStatus, UnansweredQuestion, UnansweredStatus
from main import app

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "mysql+pymysql://root:fumate@localhost:3306/mb_ai_core_test?charset=utf8mb4")
engine = create_engine(DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def setup_module():
    # MySQL：外键约束需先 SET FOREIGN_KEY_CHECKS=0 才能按任意顺序 DROP；MySQL 无 DROP TYPE
    with engine.begin() as conn:
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0;")
        conn.exec_driver_sql("DROP TABLE IF EXISTS knowledge_items;")
        conn.exec_driver_sql("DROP TABLE IF EXISTS unanswered_questions;")
        conn.exec_driver_sql("DROP TABLE IF EXISTS sales_cases;")
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1;")
    Base.metadata.create_all(bind=engine)


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
