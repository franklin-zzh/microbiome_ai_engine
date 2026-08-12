"""知识库域（横切共享，mb_ai_engine.core_*）：REST 路由

- router（/knowledge）：CS 原始文档上传（asset/document/version 生命周期）与销售案例提交
- admin_router（/admin）：CS 文档审核 / 下载 / 列表
- cs_router（/cs）：未解答问题捕获（客服缺口反哺知识库，URL 前缀兼容旧版）

旧知识项链路（core_knowledge_items 手工录入 + core_sync_tasks 同步对账）已于 2026-08-12 下线，
CS 知识一律走「原文件 → 逻辑文档/版本 → Dify（Pipeline 或 create_by_file）」链路。
"""
import hashlib
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import structured_log
from app.core.security import require_admin, require_internal_key
from app.knowledge.models import (
    KnowledgeAsset,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeVersionStatus,
    SalesCase,
    SalesCaseStatus,
    UnansweredQuestion,
    UnansweredStatus,
)
from app.knowledge.schemas import (
    GenericMessageResponse,
    KnowledgeAssetOut,
    KnowledgeDocumentCreate,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentOut,
    KnowledgeDocumentReviewRequest,
    KnowledgeDocumentVersionCreate,
    KnowledgeDocumentVersionOut,
    SalesCaseOut,
    SalesCaseSubmitRequest,
    UnansweredCaptureRequest,
    UnansweredQuestionOut,
)
from app.knowledge.document_services import asset_file_path, create_document_version, publish_document_version, revoke_document_version

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])
cs_router = APIRouter(prefix="/cs", tags=["cs"])

# ============ 原始文档存储（public/uploads，SSOT；未来可切 MinIO/OSS）============
PUBLIC_ROOT = Path(__file__).resolve().parent.parent.parent / "public"
UPLOAD_DIR = PUBLIC_ROOT / "uploads"
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".md"}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB

settings = get_settings()




# ============ CS 原始文档 -> 版本 -> Dify Knowledge Pipeline ============


@router.post("/documents/assets", response_model=KnowledgeAssetOut, dependencies=[Depends(require_admin)])
def upload_cs_document_asset(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """保存不可变原文件并登记资产；此操作不会调用 Dify、不会创建线上知识。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext or 'unknown'}")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name
    digest = hashlib.sha256()
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=413, detail="File too large (max 20MB)")
                digest.update(chunk)
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    asset = KnowledgeAsset(
        original_filename=file.filename or name,
        mime_type=file.content_type,
        size_bytes=size,
        sha256=digest.hexdigest(),
        storage_provider="LOCAL",
        object_key=f"uploads/{name}",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    structured_log(
        event="cs_document_asset_uploaded",
        item_id=asset.id,
        domain="CS",
        status="SUCCESS",
        extra={"filename": asset.original_filename, "size": asset.size_bytes, "sha256": asset.sha256},
    )
    return asset


@admin_router.get("/knowledge/assets/{asset_id}/source", dependencies=[Depends(require_admin)])
def download_cs_document_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.query(KnowledgeAsset).filter(KnowledgeAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Knowledge asset not found")
    try:
        return FileResponse(asset_file_path(asset), filename=asset.original_filename, media_type=asset.mime_type)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Source asset not found") from None


@router.post("/documents", response_model=KnowledgeDocumentOut, dependencies=[Depends(require_admin)])
def create_cs_document(body: KnowledgeDocumentCreate, db: Session = Depends(get_db)):
    """创建逻辑文档及 V1.0 待审核版本；版本不是全局知识库版本号。"""
    asset = db.query(KnowledgeAsset).filter(KnowledgeAsset.id == body.asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Knowledge asset not found")
    document = KnowledgeDocument(
        domain="CS",
        title=body.title,
        category=body.category,
        doc_form=body.doc_form,
        tags=body.tags,
        created_by=body.created_by,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    create_document_version(
        db,
        document,
        asset,
        title=body.title,
        category=body.category,
        tags=body.tags,
        pipeline_version=body.pipeline_version or settings.cs_pipeline_version,
        created_by=body.created_by,
    )
    db.refresh(document)
    return document


@admin_router.post("/knowledge/documents/{document_id}/versions", response_model=KnowledgeDocumentVersionOut, dependencies=[Depends(require_admin)])
def create_cs_document_update(
    document_id: int,
    body: KnowledgeDocumentVersionCreate,
    db: Session = Depends(get_db),
):
    """从稳定 document_id 新建下一版本，绝不依据文件名或内容相似度猜旧版。"""
    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    asset = db.query(KnowledgeAsset).filter(KnowledgeAsset.id == body.asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Knowledge asset not found")
    version = create_document_version(
        db,
        document,
        asset,
        title=body.title or document.title,
        category=body.category or document.category,
        tags=body.tags if body.tags is not None else (document.tags or []),
        pipeline_version=body.pipeline_version or settings.cs_pipeline_version,
        created_by=body.created_by,
    )
    return version


@admin_router.get("/knowledge/documents", response_model=KnowledgeDocumentListResponse, dependencies=[Depends(require_admin)])
def list_cs_documents(
    version_status: str = Query(None, pattern="^(DRAFT|PENDING|APPROVED|PUBLISHED|SUPERSEDED|REJECTED|REVOKED)$"),
    category: str = Query(None, min_length=1, max_length=64),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(KnowledgeDocument).filter(KnowledgeDocument.domain == "CS")
    if category:
        query = query.filter(KnowledgeDocument.category == category)
    if version_status:
        query = query.join(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.status == version_status).distinct()
    total = query.count()
    items = query.order_by(KnowledgeDocument.updated_at.desc()).offset(skip).limit(limit).all()
    return KnowledgeDocumentListResponse(total=total, items=[KnowledgeDocumentOut.model_validate(item) for item in items])


@admin_router.get("/knowledge/documents/{document_id}", response_model=KnowledgeDocumentOut, dependencies=[Depends(require_admin)])
def get_cs_document(document_id: int, db: Session = Depends(get_db)):
    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    return document


@admin_router.post("/knowledge/document-versions/{version_id}/approve", response_model=GenericMessageResponse, dependencies=[Depends(require_admin)])
def approve_cs_document_version(
    version_id: int,
    body: KnowledgeDocumentReviewRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    version = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Knowledge document version not found")
    if version.status not in {KnowledgeVersionStatus.DRAFT, KnowledgeVersionStatus.PENDING, KnowledgeVersionStatus.APPROVED}:
        raise HTTPException(status_code=400, detail=f"Version cannot be approved from {version.status.value}")
    version.status = KnowledgeVersionStatus.APPROVED
    version.reviewed_by = body.operator
    version.review_note = body.review_note
    version.reviewed_at = datetime.now()
    db.commit()
    # 审核结论与发布完成分离：后台任务仅驱动 Pipeline 投影。
    background_tasks.add_task(publish_document_version, version.id)
    return GenericMessageResponse(message="Approved; Dify Pipeline publish scheduled")


@admin_router.post("/knowledge/document-versions/{version_id}/retry-publish", response_model=GenericMessageResponse, dependencies=[Depends(require_admin)])
def retry_cs_document_publish(
    version_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """重试一个已审核但 Dify Pipeline 失败的版本，不会改写其内容快照。"""
    version = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Knowledge document version not found")
    if version.status != KnowledgeVersionStatus.APPROVED:
        raise HTTPException(status_code=400, detail="Only APPROVED versions can retry publication")
    background_tasks.add_task(publish_document_version, version.id)
    return GenericMessageResponse(message="Dify Pipeline retry scheduled")


@admin_router.post("/knowledge/document-versions/{version_id}/reject", response_model=GenericMessageResponse, dependencies=[Depends(require_admin)])
def reject_cs_document_version(
    version_id: int,
    body: KnowledgeDocumentReviewRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    version = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Knowledge document version not found")
    if version.status == KnowledgeVersionStatus.PUBLISHED:
        raise HTTPException(status_code=400, detail="Published versions must be revoked, not rejected")
    version.status = KnowledgeVersionStatus.REJECTED
    version.reviewed_by = body.operator
    version.review_note = body.review_note
    version.reviewed_at = datetime.now()
    db.commit()
    if version.publish_targets:
        background_tasks.add_task(revoke_document_version, version.id, KnowledgeVersionStatus.REJECTED)
    return GenericMessageResponse(message="Rejected")


@admin_router.post("/knowledge/document-versions/{version_id}/revoke", response_model=GenericMessageResponse, dependencies=[Depends(require_admin)])
def revoke_cs_document_version(
    version_id: int,
    body: KnowledgeDocumentReviewRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    version = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Knowledge document version not found")
    if version.status != KnowledgeVersionStatus.PUBLISHED:
        raise HTTPException(status_code=400, detail="Only PUBLISHED versions can be revoked")
    version.reviewed_by = body.operator
    version.review_note = body.review_note
    db.commit()
    background_tasks.add_task(revoke_document_version, version.id)
    return GenericMessageResponse(message="Revocation scheduled")


@admin_router.get("/knowledge/document-versions/{version_id}/diff", dependencies=[Depends(require_admin)])
def diff_cs_document_versions(
    version_id: int,
    against_version_id: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    """精确块差异：按保存的 QA/文本哈希比较，不依赖文件名或 Dify 搜索猜关联。"""
    current = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == version_id).first()
    against = db.query(KnowledgeDocumentVersion).filter(KnowledgeDocumentVersion.id == against_version_id).first()
    if not current or not against or current.document_id != against.document_id:
        raise HTTPException(status_code=400, detail="Versions must belong to the same knowledge document")

    def block_map(version: KnowledgeDocumentVersion):
        return {
            block.content_sha256: {
                "segment_id": block.dify_segment_id,
                "type": block.block_type.value,
                "question": block.question,
                "answer": block.answer,
                "content": block.content,
            }
            for target in version.publish_targets
            for block in target.blocks
        }

    left, right = block_map(current), block_map(against)
    return {
        "version_id": current.id,
        "against_version_id": against.id,
        "added": [left[key] for key in left.keys() - right.keys()],
        "removed": [right[key] for key in right.keys() - left.keys()],
        "unchanged_count": len(left.keys() & right.keys()),
    }




@router.post("/sales-case", response_model=SalesCaseOut, dependencies=[Depends(require_admin)])
def submit_sales_case(
    body: SalesCaseSubmitRequest,
    db: Session = Depends(get_db),
):
    """提交一条销售案例（仅登录用户）"""
    case = SalesCase(
        submitted_by=body.submitted_by,
        raw_chat_log=body.raw_chat_log,
        customer_type=body.customer_type,
        core_objection=body.core_objection,
        breakthrough_logic=body.breakthrough_logic,
        follow_up_script=body.follow_up_script,
        extracted_summary=body.extracted_summary,
        status=SalesCaseStatus.PENDING,
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    # 旧版此处会同步生成一条待审核的 SALES 知识项；旧知识项链路已下线（2026-08-12），
    # 销售域后续规划为「聊天记录直接导入」工作流形态，此处仅保留案例原始记录。
    structured_log(
        event="sales_case_submitted",
        item_id=case.id,
        domain="SALES",
        source_type="SALES_CASE",
        status="PENDING",
        extra={"submitted_by": body.submitted_by},
    )
    return case




# ============ 未解答问题捕获（客服缺口反哺） ============


@cs_router.post("/unanswered/capture", response_model=GenericMessageResponse, dependencies=[Depends(require_internal_key)])
def capture_unanswered(
    body: UnansweredCaptureRequest,
    db: Session = Depends(get_db),
):
    record = UnansweredQuestion(
        user_query=body.user_query,
        normalized_query=body.normalized_query or body.user_query,
        context=body.context,
        match_score=str(body.match_score) if body.match_score is not None else None,
        status=UnansweredStatus.OPEN,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    structured_log(
        event="unanswered_captured",
        item_id=record.id,
        domain="CS",
        source_type="CS_GAP",
        status="OPEN",
        extra={
            "match_score": body.match_score,
            "risk_flag": body.risk_flag,
            "user_query": body.user_query,
        },
    )
    return GenericMessageResponse(message="Unanswered question captured")


@cs_router.get("/unanswered", response_model=list[UnansweredQuestionOut], dependencies=[Depends(require_admin)])
def list_unanswered(
    status: str = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    query = db.query(UnansweredQuestion)
    if status:
        query = query.filter(UnansweredQuestion.status == status)
    return query.order_by(UnansweredQuestion.created_at.desc()).offset(skip).limit(limit).all()


@cs_router.post("/unanswered/{question_id}/resolve", response_model=GenericMessageResponse, dependencies=[Depends(require_admin)])
def resolve_unanswered(
    question_id: int,
    db: Session = Depends(get_db),
):
    record = db.query(UnansweredQuestion).filter(UnansweredQuestion.id == question_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Question not found")
    record.resolve()
    db.commit()
    return GenericMessageResponse(message="Resolved")
