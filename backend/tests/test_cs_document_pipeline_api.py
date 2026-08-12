"""CS source-document lifecycle tests.

These tests keep the Dify server mocked: they verify the durable associations that
make a later Pipeline run traceable, instead of testing Dify internals.
"""
import hashlib

from app.knowledge.models import KnowledgeDocumentVersion, KnowledgeVersionStatus
from tests.test_knowledge_api import ADMIN_HEADERS, TestingSessionLocal, client


def _upload_asset(name: str, content: bytes) -> int:
    response = client.post(
        "/api/v1/knowledge/documents/assets",
        headers=ADMIN_HEADERS,
        files={"file": (name, content, "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["sha256"] == hashlib.sha256(content).hexdigest()
    return response.json()["id"]


def test_cs_document_has_stable_id_and_incrementing_versions():
    first_asset = _upload_asset("faq-v1.pdf", b"first version")
    created = client.post(
        "/api/v1/knowledge/documents",
        headers=ADMIN_HEADERS,
        json={
            "title": "检测报告 FAQ",
            "asset_id": first_asset,
            "category": "report.faq",
            "tags": ["报告"],
            "created_by": "pytest",
        },
    )
    assert created.status_code == 200
    document = created.json()
    assert document["domain"] == "CS"
    assert document["versions"][0]["revision"] == 1
    assert document["versions"][0]["status"] == "PENDING"

    second_asset = _upload_asset("faq-v2.pdf", b"second version")
    updated = client.post(
        f"/api/v1/admin/knowledge/documents/{document['id']}/versions",
        headers=ADMIN_HEADERS,
        json={"asset_id": second_asset, "created_by": "pytest", "title": "检测报告 FAQ（修订）"},
    )
    assert updated.status_code == 200
    assert updated.json()["document_id"] == document["id"]
    assert updated.json()["revision"] == 2

    detail = client.get(f"/api/v1/admin/knowledge/documents/{document['id']}", headers=ADMIN_HEADERS)
    assert detail.status_code == 200
    assert [item["revision"] for item in detail.json()["versions"]] == [1, 2]


def test_cs_document_approval_schedules_pipeline_without_marking_published(monkeypatch):
    asset_id = _upload_asset("policy.pdf", b"policy content")
    created = client.post(
        "/api/v1/knowledge/documents",
        headers=ADMIN_HEADERS,
        json={"title": "退款政策", "asset_id": asset_id, "created_by": "pytest"},
    )
    version_id = created.json()["versions"][0]["id"]
    called = {}

    def fake_publish(published_version_id):
        called["version_id"] = published_version_id

    import app.knowledge.router as knowledge_router

    monkeypatch.setattr(knowledge_router, "publish_document_version", fake_publish)
    approved = client.post(
        f"/api/v1/admin/knowledge/document-versions/{version_id}/approve",
        headers=ADMIN_HEADERS,
        json={"operator": "reviewer", "review_note": "合规通过"},
    )
    assert approved.status_code == 200
    assert called["version_id"] == version_id

    db = TestingSessionLocal()
    try:
        version = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == version_id).first()
        assert version.status == KnowledgeVersionStatus.APPROVED
        assert version.reviewed_by == "reviewer"
        # 人审通过不等于线上已经可被召回；真正发布由 Pipeline 成功后置 PUBLISHED。
        assert version.status != KnowledgeVersionStatus.PUBLISHED
    finally:
        db.close()
