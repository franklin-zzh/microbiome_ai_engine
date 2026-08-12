"""CS document_services 发布/撤销链路测试。

Dify 客户端全部 mock：这里验证的是 MySQL 中的持久化状态机——
发布成功后的版本/目标/块快照、替代旧版本、失败可重试、撤销清理，
而不是 Dify 内部行为（那部分见 test_dify_pipeline_client.py）。
"""
import hashlib
import pytest

from app.knowledge.document_services import publish_document_version, revoke_document_version
from app.knowledge.models import (
    DifyPublishTarget,
    KnowledgeBlockType,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    KnowledgeDocumentVersion,
    KnowledgeVersionStatus,
    PublishStatus,
)
from tests.test_knowledge_api import ADMIN_HEADERS, TestingSessionLocal, client

SEGMENTS_V1 = [
    {"id": "seg-1", "content": "报告多久出？", "answer": "5-7 个工作日", "position": 1},
    {"id": "seg-2", "content": "报告以小程序通知为准，不另行电话告知。", "position": 2},
]
SEGMENTS_V2 = [
    {"id": "seg-3", "content": "报告出具时间可以加急吗？", "answer": "加急 24 小时", "position": 1},
    {"id": "seg-2", "content": "报告以小程序通知为准，不另行电话告知。", "position": 2},
]


@pytest.fixture
def pipeline_mocks(monkeypatch):
    """把发布链路的 Dify 调用全部替换为可控假实现，并让自建 session 落在测试库。"""
    import app.knowledge.document_services as ds

    monkeypatch.setattr(ds, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        ds,
        "get_settings",
        lambda: type("S", (), {"cs_qa_dataset_id": "dataset-cs", "cs_doc_dataset_id": "dataset-doc"})(),
    )
    monkeypatch.setattr(ds, "resolve_local_file_node_id", lambda dataset_id: "file-node-1")

    state = {
        "uploads": 0,
        "runs": 0,
        "deletes": 0,
        "create_by_files": 0,
        "wait_sequence": ["completed"],
        "segments": SEGMENTS_V1,
    }

    def fake_upload(source, filename):
        state["uploads"] += 1
        return {"id": f"file-{state['uploads']}"}

    def fake_run(dataset_id, file_id, filename, start_node_id, **kwargs):
        state["runs"] += 1
        return {"workflow_run_id": f"run-{state['runs']}", "outputs": {"document_id": f"doc-{state['runs']}"}}

    def fake_wait(dataset_id, document_id):
        return state["wait_sequence"].pop(0) if len(state["wait_sequence"]) > 1 else state["wait_sequence"][0]

    def fake_segments(dataset_id, document_id):
        return state["segments"]

    def fake_delete(dataset_id, document_id):
        state["deletes"] += 1

    def fake_create_by_file(dataset_id, file_path, filename, doc_form="text_model", process_rule=None):
        state["create_by_files"] += 1
        return f"doc-file-{state['create_by_files']}"

    monkeypatch.setattr(ds, "upload_pipeline_file", fake_upload)
    monkeypatch.setattr(ds, "run_pipeline", fake_run)
    monkeypatch.setattr(ds, "wait_pipeline_document_indexed", fake_wait)
    monkeypatch.setattr(ds, "list_document_segments", fake_segments)
    monkeypatch.setattr(ds, "delete_pipeline_document", fake_delete)
    monkeypatch.setattr(ds, "create_by_file", fake_create_by_file)
    return state


def _upload_asset(name: str, content: bytes) -> int:
    response = client.post(
        "/api/v1/knowledge/documents/assets",
        headers=ADMIN_HEADERS,
        files={"file": (name, content, "application/pdf")},
    )
    assert response.status_code == 200
    return response.json()["id"]


def _create_version(title: str = "检测报告 FAQ", name: str = "faq-v1.pdf", content: bytes = b"v1 body", document_id: int = None):
    """创建逻辑文档及其 V1.0（document_id=None），或给已有文档新增下一版本。"""
    asset_id = _upload_asset(name, content)
    if document_id is None:
        created = client.post(
            "/api/v1/knowledge/documents",
            headers=ADMIN_HEADERS,
            json={"title": title, "asset_id": asset_id, "category": "report.faq", "created_by": "pytest"},
        )
        assert created.status_code == 200
        return created.json()["id"], created.json()["versions"][0]["id"]
    updated = client.post(
        f"/api/v1/admin/knowledge/documents/{document_id}/versions",
        headers=ADMIN_HEADERS,
        json={"asset_id": asset_id, "title": title, "created_by": "pytest"},
    )
    assert updated.status_code == 200
    return document_id, updated.json()["id"]


def _set_approved(version_id: int) -> None:
    db = TestingSessionLocal()
    try:
        version = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == version_id).first()
        version.status = KnowledgeVersionStatus.APPROVED
        db.commit()
    finally:
        db.close()


def _get_version(version_id: int) -> KnowledgeDocumentVersion:
    """关闭 session 后仍可读：文档、发布目标与块快照全部 eager load。"""
    from sqlalchemy.orm import joinedload

    db = TestingSessionLocal()
    try:
        return (
            db.query(KnowledgeDocumentVersion)
            .options(
                joinedload(KnowledgeDocumentVersion.document),
                joinedload(KnowledgeDocumentVersion.publish_targets).joinedload(DifyPublishTarget.blocks),
            )
            .filter(KnowledgeDocumentVersion.id == version_id)
            .first()
        )
    finally:
        db.close()


def test_publish_full_flow_records_blocks_and_publishes_version(pipeline_mocks):
    document_id, version_id = _create_version()
    _set_approved(version_id)

    publish_document_version(version_id)

    version = _get_version(version_id)
    assert version.status == KnowledgeVersionStatus.PUBLISHED
    assert version.document.current_version_id == version.id
    target = version.publish_targets[0]
    assert target.status == PublishStatus.COMPLETED
    assert target.dataset_id == "dataset-cs"
    assert target.pipeline_start_node_id == "file-node-1"
    assert target.dify_file_id == "file-1"
    assert target.pipeline_run_id == "run-1"
    assert target.dify_document_id == "doc-1"
    assert target.attempts == 1
    assert target.indexed_at is not None

    blocks = target.blocks
    assert len(blocks) == 2
    qa_block = next(b for b in blocks if b.block_type == KnowledgeBlockType.QA)
    text_block = next(b for b in blocks if b.block_type == KnowledgeBlockType.TEXT)
    assert qa_block.question == "报告多久出？"
    assert qa_block.answer == "5-7 个工作日"
    assert qa_block.dify_segment_id == "seg-1"
    assert text_block.content == "报告以小程序通知为准，不另行电话告知。"
    # 块快照可追溯：segment_id -> dify_document_id -> version
    assert pipeline_mocks["runs"] == 1


def test_publish_second_version_supersedes_first(pipeline_mocks):
    document_id, v1_id = _create_version(title="退款政策", name="policy-v1.pdf", content=b"policy v1")
    _set_approved(v1_id)
    publish_document_version(v1_id)

    _, v2_id = _create_version(title="退款政策（修订）", name="policy-v2.pdf", content=b"policy v2", document_id=document_id)
    _set_approved(v2_id)
    publish_document_version(v2_id)

    v1 = _get_version(v1_id)
    v2 = _get_version(v2_id)
    assert v1.status == KnowledgeVersionStatus.SUPERSEDED
    assert v1.publish_targets[0].status == PublishStatus.DELETED  # 旧投影已从 Dify 删除
    assert v2.status == KnowledgeVersionStatus.PUBLISHED
    assert v2.document.current_version_id == v2.id
    assert pipeline_mocks["deletes"] == 1
    # 发布后才把线上展示元数据切到新版本快照
    assert v2.document.title == "退款政策（修订）"


def test_publish_failure_marks_target_failed_version_stays_approved(pipeline_mocks, monkeypatch):
    import app.knowledge.document_services as ds
    from app.clients.dify_knowledge_client import DifyPipelineError

    def failing_run(*args, **kwargs):
        raise DifyPipelineError("pipeline exploded")

    monkeypatch.setattr(ds, "run_pipeline", failing_run)

    _, version_id = _create_version()
    _set_approved(version_id)
    publish_document_version(version_id)

    version = _get_version(version_id)
    assert version.status == KnowledgeVersionStatus.APPROVED  # 人审结论不被技术失败改写
    target = version.publish_targets[0]
    assert target.status == PublishStatus.FAILED
    assert "pipeline exploded" in target.last_error
    assert target.attempts == 1


def test_publish_retry_resumes_existing_document_without_duplicate(pipeline_mocks):
    """上次 run 已创建 Dify 文档但索引超时；重试应续跑，而不是重复创建文档。"""
    pipeline_mocks["wait_sequence"] = ["timeout", "completed"]
    _, version_id = _create_version()
    _set_approved(version_id)

    publish_document_version(version_id)  # 第一次：run 成功、索引超时 -> FAILED
    version = _get_version(version_id)
    assert version.publish_targets[0].status == PublishStatus.FAILED
    assert version.publish_targets[0].dify_document_id == "doc-1"
    assert pipeline_mocks["uploads"] == 1

    publish_document_version(version_id)  # 重试：直接等待既有文档索引完成
    version = _get_version(version_id)
    assert version.status == KnowledgeVersionStatus.PUBLISHED
    assert version.publish_targets[0].status == PublishStatus.COMPLETED
    assert version.publish_targets[0].dify_document_id == "doc-1"
    assert pipeline_mocks["uploads"] == 1  # 没有重复上传
    assert pipeline_mocks["runs"] == 1  # 没有重复运行 Pipeline
    assert pipeline_mocks["deletes"] == 0


def test_publish_retry_recreates_when_existing_document_index_error(pipeline_mocks):
    """旧 Dify 文档索引失败时，重试删除旧文档并按新建路径重建。"""
    # 第一次发布 wait#1=error -> FAILED；重试时幂等分支 wait#2=error -> 删除重建；
    # 新文档 wait#3=completed。
    pipeline_mocks["wait_sequence"] = ["error", "error", "completed"]
    _, version_id = _create_version()
    _set_approved(version_id)

    publish_document_version(version_id)
    version = _get_version(version_id)
    assert version.publish_targets[0].status == PublishStatus.FAILED
    assert version.publish_targets[0].dify_document_id == "doc-1"

    publish_document_version(version_id)
    version = _get_version(version_id)
    assert version.status == KnowledgeVersionStatus.PUBLISHED
    assert version.publish_targets[0].dify_document_id == "doc-2"  # 重建后的新文档
    assert pipeline_mocks["deletes"] == 1
    assert pipeline_mocks["uploads"] == 2
    assert pipeline_mocks["runs"] == 2


def test_revoke_document_version_deletes_projection_and_archives_document(pipeline_mocks):
    _, version_id = _create_version()
    _set_approved(version_id)
    publish_document_version(version_id)

    revoke_document_version(version_id)

    version = _get_version(version_id)
    assert version.status == KnowledgeVersionStatus.REVOKED
    assert version.publish_targets[0].status == PublishStatus.DELETED
    assert version.document.current_version_id is None
    assert version.document.status == KnowledgeDocumentStatus.ARCHIVED
    assert pipeline_mocks["deletes"] == 1


def test_document_version_diff_compares_block_snapshots(pipeline_mocks):
    """线上这条 QA 从哪份文件来的：块快照哈希比较给出 added/removed/unchanged。"""
    doc_id, v1_id = _create_version(name="diff-v1.pdf", content=b"diff v1")
    _set_approved(v1_id)
    publish_document_version(v1_id)

    pipeline_mocks["segments"] = SEGMENTS_V2
    _, v2_id = _create_version(name="diff-v2.pdf", content=b"diff v2", document_id=doc_id)
    _set_approved(v2_id)
    publish_document_version(v2_id)

    response = client.get(
        f"/api/v1/admin/knowledge/document-versions/{v2_id}/diff?against_version_id={v1_id}",
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["added"] == [
        {
            "segment_id": "seg-3",
            "type": "QA",
            "question": "报告出具时间可以加急吗？",
            "answer": "加急 24 小时",
            "content": "报告出具时间可以加急吗？",
        }
    ]
    assert data["removed"] == [
        {
            "segment_id": "seg-1",
            "type": "QA",
            "question": "报告多久出？",
            "answer": "5-7 个工作日",
            "content": "报告多久出？",
        }
    ]
    assert data["unchanged_count"] == 1  # 未变的 TEXT 块


# ============ DOC 知识库：text_model 直传（create_by_file） ============


def _create_text_version(title="公司产品手册", name="manual-v1.pdf", content=b"manual v1"):
    """创建 doc_form=text_model 的逻辑文档（上传时形态单选，走 CS_DOC 直传路径）。"""
    asset_id = _upload_asset(name, content)
    created = client.post(
        "/api/v1/knowledge/documents",
        headers=ADMIN_HEADERS,
        json={
            "title": title,
            "asset_id": asset_id,
            "doc_form": "text_model",
            "category": "product.manual",
            "created_by": "pytest",
        },
    )
    assert created.status_code == 200
    data = created.json()
    assert data["doc_form"] == "text_model"
    return data["id"], data["versions"][0]["id"]


def test_publish_text_model_uses_create_by_file_direct(pipeline_mocks):
    """text_model 文档：create_by_file 直传 CS_DOC（不走 Pipeline），块回读全为 TEXT。"""
    pipeline_mocks["segments"] = [
        {"id": "t1", "content": "第一章 产品介绍", "position": 1},
        {"id": "t2", "content": "第二章 使用说明", "position": 2},
    ]
    document_id, version_id = _create_text_version()
    _set_approved(version_id)
    publish_document_version(version_id)

    version = _get_version(version_id)
    assert version.status == KnowledgeVersionStatus.PUBLISHED
    assert version.document.doc_form.value == "text_model"
    target = version.publish_targets[0]
    assert target.status == PublishStatus.COMPLETED
    assert target.dataset_id == "dataset-doc"  # 单选：只进 DOC 库
    assert target.pipeline_start_node_id is None  # 直传路径无 start node
    assert target.pipeline_run_id is None
    assert target.run_output is None
    assert target.dify_document_id == "doc-file-1"
    assert pipeline_mocks["create_by_files"] == 1
    assert pipeline_mocks["runs"] == 0  # 不走 Pipeline
    assert pipeline_mocks["uploads"] == 0
    assert len(target.blocks) == 2
    assert all(b.block_type == KnowledgeBlockType.TEXT for b in target.blocks)
    assert target.blocks[0].content == "第一章 产品介绍"
    # 版本快照记录形态（审核视图可追溯）
    assert version.content_snapshot["doc_form"] == "text_model"

    # API 回归：直传路径 pipeline_start_node_id=None 时列表接口不应 500
    listing = client.get("/api/v1/admin/knowledge/documents?limit=50", headers=ADMIN_HEADERS)
    assert listing.status_code == 200
    listed = next(d for d in listing.json()["items"] if d["id"] == document_id)
    assert listed["doc_form"] == "text_model"
    assert listed["versions"][0]["publish_targets"][0]["pipeline_start_node_id"] is None


def test_publish_text_model_rejects_when_doc_dataset_missing(pipeline_mocks, monkeypatch):
    """CS_DOC_DATASET_ID 未配置时拒绝发布（不回落 QA 库），target 置 FAILED。"""
    import app.knowledge.document_services as ds

    monkeypatch.setattr(
        ds,
        "get_settings",
        lambda: type("S", (), {"cs_qa_dataset_id": "dataset-cs", "cs_doc_dataset_id": ""})(),
    )

    _, version_id = _create_text_version()
    _set_approved(version_id)
    publish_document_version(version_id)

    version = _get_version(version_id)
    assert version.status == KnowledgeVersionStatus.APPROVED  # 人审结论不被技术失败改写
    assert version.publish_targets == []  # 目标库未配置：不创建投影，错误记入日志（cs_document_publish_failed）
    assert pipeline_mocks["create_by_files"] == 0
