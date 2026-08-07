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
from app.knowledge.models import KnowledgeItem, KnowledgeStatus, UnansweredQuestion, UnansweredStatus
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
    }, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PENDING"
    assert data["domain"] == "CS"


def test_approve_knowledge_triggers_sync_mock(monkeypatch):
    captured = {}

    def fake_create(*args, **kwargs):
        captured["called"] = True
        captured["args"] = kwargs
        return "fake-doc-id"

    def fake_wait(*args, **kwargs):
        return "completed"

    import app.knowledge.services as knowledge_services
    monkeypatch.setattr(knowledge_services, "create_document", fake_create)
    monkeypatch.setattr(knowledge_services, "wait_document_indexed", fake_wait)

    submit = client.post("/api/v1/knowledge/submit", json={
        "domain": "CS",
        "title": "待审核 FAQ",
        "question": "问题",
        "answer": "答案",
    }, headers=ADMIN_HEADERS)
    item_id = submit.json()["id"]

    response = client.post(f"/api/v1/admin/knowledge/{item_id}/approve", json={"approved_by": "admin"}, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["message"] == "Approved and sync scheduled"
    assert captured.get("called") is True

    # 任务化：core_sync_tasks 应有 COMPLETED 记录，item 同步状态 COMPLETED。
    # 注意：必须用 TestingSessionLocal（测试库）——模块级 SessionLocal 指向开发库。
    from app.knowledge.models import KnowledgeItem, SyncTask, SyncTaskStatus
    db = TestingSessionLocal()
    try:
        item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
        task = db.query(SyncTask).filter(SyncTask.item_id == item_id).first()
        assert item.vector_doc_id == "fake-doc-id"
        assert item.sync_status.value == "COMPLETED"
        assert task is not None and task.status == SyncTaskStatus.COMPLETED
    finally:
        db.close()


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


def test_list_knowledge():
    response = client.get("/api/v1/admin/knowledge?domain=CS&status=PENDING", headers=ADMIN_HEADERS)
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
    }, headers=ADMIN_HEADERS)
    assert submit.status_code == 200
    assert submit.json()["category"] == "product.probiotics"

    # 未指定 category 时兜底为 GENERAL（test_submit_knowledge 提交的条目）
    resp = client.get("/api/v1/admin/knowledge?category=GENERAL", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1

    # 按 category 精确筛选
    resp = client.get("/api/v1/admin/knowledge?category=product.probiotics", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert all(i["category"] == "product.probiotics" for i in data["items"])

def test_reject_knowledge():
    submit = client.post("/api/v1/knowledge/submit", json={
        "domain": "CS",
        "title": "待驳回 FAQ",
        "question": "驳回问题",
        "answer": "驳回答案",
    }, headers=ADMIN_HEADERS)
    item_id = submit.json()["id"]

    response = client.post(f"/api/v1/admin/knowledge/{item_id}/reject", json={"operator": "admin"}, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["message"] == "Rejected"

    db = TestingSessionLocal()
    try:
        item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
        assert item.status.value == "REJECTED"
        assert item.vector_doc_id is None  # 未同步过，无需删除
    finally:
        db.close()


def test_revoke_knowledge_deletes_dify_doc(monkeypatch):
    captured = {"deleted": False}

    def fake_create(*args, **kwargs):
        return "fake-doc-id"

    def fake_wait(*args, **kwargs):
        return "completed"

    def fake_delete(*args, **kwargs):
        captured["deleted"] = True
        return True

    import app.knowledge.services as knowledge_services
    monkeypatch.setattr(knowledge_services, "create_document", fake_create)
    monkeypatch.setattr(knowledge_services, "wait_document_indexed", fake_wait)
    monkeypatch.setattr(knowledge_services, "delete_document", fake_delete)

    submit = client.post("/api/v1/knowledge/submit", json={
        "domain": "CS",
        "title": "待下线 FAQ",
        "question": "下线问题",
        "answer": "下线答案",
    }, headers=ADMIN_HEADERS)
    item_id = submit.json()["id"]

    approve = client.post(f"/api/v1/admin/knowledge/{item_id}/approve", json={"approved_by": "admin"}, headers=ADMIN_HEADERS)
    assert approve.status_code == 200

    revoke = client.post(f"/api/v1/admin/knowledge/{item_id}/revoke", json={"operator": "admin"}, headers=ADMIN_HEADERS)
    assert revoke.status_code == 200
    assert "Revoked" in revoke.json()["message"]
    assert captured["deleted"] is True

    from app.knowledge.models import SyncTask, SyncTaskAction, SyncTaskStatus
    db = TestingSessionLocal()
    try:
        item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
        assert item.status.value == "REVOKED"
        assert item.vector_doc_id is None          # Dify 文档已删，引用清空
        assert item.sync_status.value == "NOT_SYNCED"
        delete_tasks = (
            db.query(SyncTask)
            .filter(SyncTask.item_id == item_id, SyncTask.action == SyncTaskAction.DELETE)
            .all()
        )
        assert any(t.status == SyncTaskStatus.COMPLETED for t in delete_tasks)
    finally:
        db.close()


def test_revoke_requires_approved():
    submit = client.post("/api/v1/knowledge/submit", json={
        "domain": "CS",
        "title": "未审核直接下线",
        "question": "问题",
        "answer": "答案",
    }, headers=ADMIN_HEADERS)
    item_id = submit.json()["id"]

    response = client.post(f"/api/v1/admin/knowledge/{item_id}/revoke", json={"operator": "admin"}, headers=ADMIN_HEADERS)
    assert response.status_code == 400
def test_upload_and_download_source_file():
    # 上传原始文档
    upload = client.post("/api/v1/knowledge/upload", headers=ADMIN_HEADERS,
                         files={"file": ("FAQ原始稿.pdf", b"%PDF-1.4 fake content", "application/pdf")})
    assert upload.status_code == 200
    data = upload.json()
    assert data["path"].startswith("uploads/")
    assert data["path"].endswith(".pdf")

    # 提交知识项时关联 source_file
    submit = client.post("/api/v1/knowledge/submit", json={
        "domain": "CS",
        "title": "带原稿 FAQ",
        "question": "原稿问题",
        "answer": "原稿答案",
        "source_file": data["path"],
    }, headers=ADMIN_HEADERS)
    assert submit.status_code == 200
    item_id = submit.json()["id"]

    # 鉴权下载原始文档
    download = client.get(f"/api/v1/admin/knowledge/{item_id}/source", headers=ADMIN_HEADERS)
    assert download.status_code == 200
    assert download.content == b"%PDF-1.4 fake content"

    # 未登录不可下载
    anon = client.get(f"/api/v1/admin/knowledge/{item_id}/source")
    assert anon.status_code in (401, 403)

    # 不支持的类型被拒
    bad = client.post("/api/v1/knowledge/upload", headers=ADMIN_HEADERS,
                      files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400

    # 清理测试上传文件
    import shutil
    from pathlib import Path
    shutil.rmtree(Path(__file__).resolve().parent.parent / "public" / "uploads", ignore_errors=True)
    (Path(__file__).resolve().parent.parent / "public" / "uploads").mkdir(parents=True)
    (Path(__file__).resolve().parent.parent / "public" / "uploads" / ".gitkeep").write_text("")