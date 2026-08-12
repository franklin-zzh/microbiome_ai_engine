import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.knowledge.models import SalesCase, SalesCaseStatus, UnansweredQuestion, UnansweredStatus
from main import app

settings = get_settings()
# conftest 已把 DATABASE_URL 指向 mb_ai_engine_test；此处直接取测试库连接串
engine = create_engine(settings.test_database_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# P0 鉴权：受保护接口带 token / 内部密钥访问
ADMIN_HEADERS = {"Authorization": f"Bearer {create_access_token('pytest-admin')}"}
INTERNAL_HEADERS = {"X-API-Key": settings.internal_api_key}


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
# 表结构由 conftest.migrated_test_db 通过 `alembic upgrade head` 建好。
# 旧知识项链路（/knowledge/submit、/admin/knowledge 审核、/knowledge/upload）已于
# 2026-08-12 下线并删表（core_knowledge_items / core_sync_tasks），对应测试随之移除；
# CS 文档 pipeline 的 API 测试见 test_cs_document_pipeline_api.py。
client = TestClient(app)


def test_capture_unanswered():
    response = client.post("/api/v1/cs/unanswered/capture", json={
        "user_query": "测试未解答问题",
        "match_score": 0.45,
        "context": {"channel": "dify"},
    }, headers=INTERNAL_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Unanswered question captured"


def test_list_unanswered():
    response = client.get("/api/v1/cs/unanswered?status=OPEN", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_resolve_unanswered():
    records = client.get("/api/v1/cs/unanswered?status=OPEN", headers=ADMIN_HEADERS).json()
    assert records, "需要先有未解答记录（test_capture_unanswered 前置）"
    qid = records[0]["id"]
    response = client.post(f"/api/v1/cs/unanswered/{qid}/resolve", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["message"] == "Resolved"

    db = TestingSessionLocal()
    try:
        record = db.query(UnansweredQuestion).filter(UnansweredQuestion.id == qid).first()
        assert record.status == UnansweredStatus.RESOLVED
        assert record.resolved_at is not None
    finally:
        db.close()


def test_submit_sales_case_no_knowledge_item():
    """旧链路下线后：销售案例仅存 SalesCase 记录，不再自动生成 KnowledgeItem（该表已删）。"""
    response = client.post("/api/v1/knowledge/sales-case", json={
        "submitted_by": "pytest",
        "raw_chat_log": "客户觉得价格太贵",
        "customer_type": "价格敏感型",
        "core_objection": "价格",
        "breakthrough_logic": "价值对比",
        "follow_up_script": "强调长期价值",
    }, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    case_id = response.json()["id"]

    db = TestingSessionLocal()
    try:
        case = db.query(SalesCase).filter(SalesCase.id == case_id).first()
        assert case is not None
        assert case.status == SalesCaseStatus.PENDING
        assert case.submitted_by == "pytest"
    finally:
        db.close()
