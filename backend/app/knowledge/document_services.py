"""CS source-document versioning and Dify knowledge publication.

The database is the source of truth. Dify is a derived, rebuildable projection:
no workflow output is used to infer which logical document/version it belongs to.

发布按文档级 doc_form 分派（一个文档一个形态一个目标库，绝不双写）：
- qa_model → CS_QA_DATASET_ID，跑 Knowledge Pipeline（FILE -> QA Processor -> KB）
- text_model / hierarchical_model → CS_DOC_DATASET_ID，create_by_file 直传（Dify 原生切割）
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.clients.dify_knowledge_client import (
    DifyPipelineError,
    create_by_file,
    delete_pipeline_document,
    extract_pipeline_identifiers,
    list_document_segments,
    resolve_local_file_node_id,
    run_pipeline,
    upload_pipeline_file,
    wait_pipeline_document_indexed,
)
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import structured_log
from app.knowledge.models import (
    DifyDocumentBlock,
    DifyPublishTarget,
    KnowledgeAsset,
    KnowledgeBlockType,
    KnowledgeDocForm,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    KnowledgeDocumentVersion,
    KnowledgeVersionStatus,
    PublishStatus,
)


PUBLIC_ROOT = Path(__file__).resolve().parent.parent.parent / "public"


def asset_file_path(asset: KnowledgeAsset) -> Path:
    """Resolve a LOCAL asset only within the protected upload root."""
    if asset.storage_provider != "LOCAL":
        raise ValueError(f"Unsupported storage provider: {asset.storage_provider}")
    path = (PUBLIC_ROOT / asset.object_key).resolve()
    if not path.is_relative_to(PUBLIC_ROOT.resolve()) or not path.is_file():
        raise FileNotFoundError(f"Knowledge asset missing: {asset.id}")
    return path


def _sha256_bytes(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def next_revision(db: Session, document_id: int) -> int:
    newest = (
        db.query(KnowledgeDocumentVersion.revision)
        .filter(KnowledgeDocumentVersion.document_id == document_id)
        .order_by(KnowledgeDocumentVersion.revision.desc())
        .first()
    )
    return (newest[0] if newest else 0) + 1


def create_document_version(
    db: Session,
    document: KnowledgeDocument,
    asset: KnowledgeAsset,
    *,
    title: str,
    category: str,
    tags: list[str],
    pipeline_version: str,
    created_by: str,
) -> KnowledgeDocumentVersion:
    snapshot = {
        "title": title,
        "category": category,
        "tags": tags,
        "asset_filename": asset.original_filename,
        "source_domain": getattr(asset, "source_domain", None),
        "source_url": getattr(asset, "source_url", None),
        "doc_form": document.doc_form.value,
    }
    version = KnowledgeDocumentVersion(
        document_id=document.id,
        revision=next_revision(db, document.id),
        asset_id=asset.id,
        content_sha256=asset.sha256,
        content_snapshot=snapshot,
        pipeline_version=pipeline_version,
        status=KnowledgeVersionStatus.PENDING,
        created_by=created_by,
    )
    db.add(version)
    # 已发布文档不能因一个待审更新而提前变更展示元数据；首版例外。
    if version.revision == 1:
        document.title = title
        document.category = category
        document.tags = tags
    db.commit()
    db.refresh(version)
    return version


def dataset_id_for_doc_form(doc_form: KnowledgeDocForm, settings=None) -> str:
    """形态 → 目标知识库映射（单选）：qa_model → 问答库，text/hierarchical → 长文档库。

    目标库未配置时拒绝发布（不回落另一库，避免 QA 与文本混库）。
    """
    settings = settings or get_settings()
    if doc_form == KnowledgeDocForm.QA_MODEL:
        if not settings.cs_qa_dataset_id:
            raise DifyPipelineError("CS_QA_DATASET_ID is not configured")
        return settings.cs_qa_dataset_id
    if doc_form in (KnowledgeDocForm.TEXT_MODEL, KnowledgeDocForm.HIERARCHICAL_MODEL):
        if not settings.cs_doc_dataset_id:
            raise DifyPipelineError("CS_DOC_DATASET_ID is not configured")
        return settings.cs_doc_dataset_id
    raise DifyPipelineError(f"Unsupported doc_form: {doc_form}")


def ensure_publish_target(db: Session, version: KnowledgeDocumentVersion) -> DifyPublishTarget:
    """按文档 doc_form 选定唯一目标 dataset 并创建/复用发布投影。"""
    doc_form = version.document.doc_form
    dataset_id = dataset_id_for_doc_form(doc_form)
    existing = (
        db.query(DifyPublishTarget)
        .filter(
            DifyPublishTarget.version_id == version.id,
            DifyPublishTarget.dataset_id == dataset_id,
        )
        .order_by(DifyPublishTarget.id.desc())
        .first()
    )
    if existing:
        if existing.status in {PublishStatus.FAILED, PublishStatus.NOT_SYNCED}:
            existing.status = PublishStatus.QUEUED
            existing.last_error = None
            db.commit()
        return existing

    target = DifyPublishTarget(
        version_id=version.id,
        dataset_id=dataset_id,
        # 直传路径无 Pipeline start node（create_by_file），qa_model 才需要解析
        pipeline_start_node_id=resolve_local_file_node_id(dataset_id) if doc_form == KnowledgeDocForm.QA_MODEL else None,
        pipeline_version=version.pipeline_version,
        status=PublishStatus.QUEUED,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def _record_blocks(db: Session, target: DifyPublishTarget, segments: list[dict]) -> None:
    db.query(DifyDocumentBlock).filter(DifyDocumentBlock.publish_target_id == target.id).delete()
    for index, segment in enumerate(segments, start=1):
        segment_id = str(segment.get("id") or segment.get("segment_id") or "")
        if not segment_id:
            continue
        content = segment.get("content")
        answer = segment.get("answer")
        question = content if answer else None
        normalized = "\n".join(str(part or "") for part in (content, answer))
        db.add(
            DifyDocumentBlock(
                publish_target_id=target.id,
                dify_segment_id=segment_id,
                position=segment.get("position") or index,
                block_type=KnowledgeBlockType.QA if answer else KnowledgeBlockType.TEXT,
                content=content,
                question=question,
                answer=answer,
                content_sha256=_sha256_bytes(normalized),
            )
        )


def _revoke_target(db: Session, target: DifyPublishTarget) -> None:
    """Delete Dify projection idempotently. Failure remains visible and retriable."""
    if target.status == PublishStatus.DELETED:
        return
    target.status = PublishStatus.DELETE_PENDING
    db.commit()
    if target.dify_document_id:
        delete_pipeline_document(target.dataset_id, target.dify_document_id)
    target.status = PublishStatus.DELETED
    target.last_error = None
    db.commit()


def _finalize_publish(
    db: Session,
    version: KnowledgeDocumentVersion,
    target: DifyPublishTarget,
    document_id: str,
) -> None:
    """记录块快照、替代旧版本并把本版本置为线上生效（发布路径的公共收尾）。"""
    _record_blocks(db, target, list_document_segments(target.dataset_id, document_id))
    _supersede_previous_version(db, version)
    version.status = KnowledgeVersionStatus.PUBLISHED
    version.reviewed_at = version.reviewed_at or datetime.now()
    version.document.title = version.content_snapshot["title"]
    version.document.category = version.content_snapshot["category"]
    version.document.tags = version.content_snapshot["tags"]
    version.document.current_version_id = version.id
    target.status = PublishStatus.COMPLETED
    target.indexed_at = datetime.now()
    target.last_error = None
    db.commit()
    structured_log(
        event="cs_document_published",
        item_id=version.document_id,
        domain="CS",
        status="COMPLETED",
        extra={
            "version_id": version.id,
            "target_id": target.id,
            "dify_document_id": document_id,
            "doc_form": version.document.doc_form.value,
        },
    )


def _supersede_previous_version(db: Session, version: KnowledgeDocumentVersion) -> None:
    document = version.document
    if not document.current_version_id or document.current_version_id == version.id:
        return
    previous = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == document.current_version_id).first()
    if not previous:
        return
    previous.status = KnowledgeVersionStatus.SUPERSEDED
    for target in previous.publish_targets:
        try:
            _revoke_target(db, target)
        except DifyPipelineError as exc:
            # 新版本已经可用；保留旧投影的删除待办，不让旧文档静默存在。
            target.status = PublishStatus.DELETE_PENDING
            target.last_error = str(exc)
            db.commit()


def publish_document_version(version_id: int, db: Optional[Session] = None) -> None:
    """Worker entry: publish an approved version to Dify, routed by document doc_form.

    - qa_model：Pipeline 路径（upload file → run published Pipeline → 轮询索引）
    - text_model / hierarchical_model：create_by_file 直传（Dify 原生切割 → 轮询索引）
    两条路径共用幂等续跑 / 失败置 FAILED / _finalize_publish 收尾。
    """
    own_session = db is None
    db = db or SessionLocal()
    try:
        version = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == version_id).first()
        if not version or version.status != KnowledgeVersionStatus.APPROVED:
            return
        target = ensure_publish_target(db, version)
        if target.status == PublishStatus.COMPLETED:
            return

        target.status = PublishStatus.RUNNING
        target.attempts += 1
        db.commit()

        # 幂等续跑：重试时若上次运行已在 Dify 留下文档，先等待其索引完成，
        # 不要再次创建重复文档；索引失败/超时则删除旧文档后重建。
        if target.dify_document_id:
            resumed_status = wait_pipeline_document_indexed(target.dataset_id, target.dify_document_id)
            if resumed_status == "completed":
                _finalize_publish(db, version, target, target.dify_document_id)
                return
            delete_pipeline_document(target.dataset_id, target.dify_document_id)
            target.dify_document_id = None
            db.commit()

        source = asset_file_path(version.asset)
        doc_form = version.document.doc_form
        title = version.document.title or version.asset.original_filename
        doc_filename = f"{title}.md" if not title.endswith((".md", ".txt", ".pdf", ".docx", ".doc")) else title

        if doc_form == KnowledgeDocForm.QA_MODEL:
            uploaded = upload_pipeline_file(source, doc_filename)
            target.dify_file_id = uploaded["id"]
            db.commit()

            output = run_pipeline(
                target.dataset_id,
                target.dify_file_id,
                doc_filename,
                target.pipeline_start_node_id,
            )
            run_id, document_id = extract_pipeline_identifiers(output)
            target.pipeline_run_id = run_id
            target.run_output = output
            target.dify_document_id = document_id
            if not document_id:
                raise DifyPipelineError(
                    "Pipeline completed without document_id. Configure the Knowledge Base node to expose document_id in its output."
                )
        else:
            # create_by_file 直传：Dify 原生切割（text_model/hierarchical_model），响应直接带 document id
            document_id = create_by_file(
                target.dataset_id,
                source,
                doc_filename,
                doc_form=doc_form.value,
            )
            target.dify_document_id = document_id

        target.status = PublishStatus.INDEXING
        db.commit()

        indexing_status = wait_pipeline_document_indexed(target.dataset_id, document_id)
        if indexing_status != "completed":
            raise DifyPipelineError(f"Dify indexing did not complete: {indexing_status}")
        _finalize_publish(db, version, target, document_id)
    except Exception as exc:  # Dify/http/storage error: durable target must never remain RUNNING
        if "target" in locals():
            target.status = PublishStatus.FAILED
            target.last_error = str(exc)
        db.commit()
        structured_log(
            event="cs_document_publish_failed",
            item_id=version_id,
            domain="CS",
            status="FAILED",
            error_msg=str(exc),
        )
    finally:
        if own_session:
            db.close()


def revoke_document_version(
    version_id: int,
    final_status: KnowledgeVersionStatus = KnowledgeVersionStatus.REVOKED,
    db: Optional[Session] = None,
) -> None:
    """Worker entry for withdrawal/rejection of a version that has a Dify projection."""
    own_session = db is None
    db = db or SessionLocal()
    try:
        version = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == version_id).first()
        if not version:
            return
        for target in version.publish_targets:
            _revoke_target(db, target)
        version.status = final_status
        if version.document.current_version_id == version.id:
            version.document.current_version_id = None
            version.document.status = KnowledgeDocumentStatus.ARCHIVED
        db.commit()
    except DifyPipelineError as exc:
        db.rollback()
        structured_log(event="cs_document_revoke_failed", item_id=version_id, domain="CS", status="FAILED", error_msg=str(exc))
    finally:
        if own_session:
            db.close()
