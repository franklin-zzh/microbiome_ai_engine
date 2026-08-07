"""知识库域（横切共享，mb_ai_engine.core_*）：REST 路由

- router（/knowledge）：知识项提交 / 销售案例提交 / 原始文档上传
- admin_router（/admin）：知识审核与列表 / 原始文档下载
- cs_router（/cs）：未解答问题捕获（客服缺口反哺知识库，URL 前缀兼容旧版）
"""
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import structured_log
from app.core.security import require_admin, require_internal_key
from app.knowledge.models import (
    KnowledgeItem,
    KnowledgeSourceType,
    KnowledgeStatus,
    SalesCase,
    SalesCaseStatus,
    UnansweredQuestion,
    UnansweredStatus,
)
from app.knowledge.schemas import (
    GenericMessageResponse,
    KnowledgeApproveRequest,
    KnowledgeItemCreate,
    KnowledgeItemOut,
    KnowledgeListResponse,
    KnowledgeRejectRequest,
    SalesCaseOut,
    SalesCaseSubmitRequest,
    UnansweredCaptureRequest,
    UnansweredQuestionOut,
)
from app.knowledge.services import sync_approved_knowledge, sync_removed_knowledge

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])
cs_router = APIRouter(prefix="/cs", tags=["cs"])

# ============ 原始文档存储（public/uploads，SSOT；未来可切 MinIO/OSS）============
PUBLIC_ROOT = Path(__file__).resolve().parent.parent.parent / "public"
UPLOAD_DIR = PUBLIC_ROOT / "uploads"
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".md"}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB

settings = get_settings()


# ============ 原始文档上传/下载 ============


@router.post("/upload", dependencies=[Depends(require_admin)])
def upload_knowledge_file(file: UploadFile = File(...)):
    """上传原始文档（审核时供人工查看）。返回相对路径，提交知识项时写入 source_file。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext or 'unknown'}")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # 服务端生成文件名（uuid），不信任用户文件名，杜绝路径穿越
    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=413, detail="File too large (max 20MB)")
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    structured_log(event="knowledge_file_uploaded", status="SUCCESS", extra={"size": size, "ext": ext})
    return {"filename": file.filename, "path": f"uploads/{name}", "size": size}


@admin_router.get("/knowledge/{item_id}/source", dependencies=[Depends(require_admin)])
def download_knowledge_source(item_id: int, db: Session = Depends(get_db)):
    """下载知识项关联的原始文档（鉴权下载；uploads 目录不直接暴露静态访问）。"""
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    if not item.source_file:
        raise HTTPException(status_code=404, detail="No source file attached")

    # 路径穿越防护：resolve 后必须仍位于 PUBLIC_ROOT 内
    path = (PUBLIC_ROOT / item.source_file).resolve()
    if not path.is_relative_to(PUBLIC_ROOT.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="Source file not found")
    return FileResponse(path, filename=Path(item.source_file).name)


# ============ 知识项提交 ============


@router.post("/submit", response_model=KnowledgeItemOut, dependencies=[Depends(require_admin)])
def submit_knowledge(
    body: KnowledgeItemCreate,
    db: Session = Depends(get_db),
):
    """提交一条待审核知识项（仅登录用户；P0 单 admin 角色，多角色见 R5）"""
    item = KnowledgeItem(
        domain=body.domain,
        source_type=body.source_type,
        status=KnowledgeStatus.PENDING,
        category=body.category,
        title=body.title,
        question=body.question,
        answer=body.answer,
        tags=body.tags or [],
        source_file=body.source_file,
        cs_gap_id=body.cs_gap_id,
        sales_case_id=body.sales_case_id,
        created_by=body.created_by,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    structured_log(
        event="knowledge_submitted",
        item_id=item.id,
        domain=item.domain.value,
        source_type=item.source_type.value,
        status="PENDING",
        extra={"created_by": body.created_by},
    )
    return item


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

    # 同时生成一条待审核的 SALES 知识项
    summary = body.extracted_summary or {}
    title = f"销售案例：{body.customer_type or '未知类型'}"
    answer = (
        f"【客户类型】{body.customer_type or '待补充'}\n"
        f"【核心抗拒点】{body.core_objection or '待补充'}\n"
        f"【成交破阻逻辑】{body.breakthrough_logic or '待补充'}\n"
        f"【推荐跟进话术】{body.follow_up_script or '待补充'}"
    )
    item = KnowledgeItem(
        domain="SALES",
        source_type=KnowledgeSourceType.SALES_CASE,
        status=KnowledgeStatus.PENDING,
        title=title,
        question=None,
        answer=answer,
        tags=["销售案例", body.customer_type] if body.customer_type else ["销售案例"],
        sales_case_id=case.id,
        created_by=body.submitted_by,
    )
    db.add(item)
    db.commit()
    db.refresh(case)

    structured_log(
        event="sales_case_submitted",
        item_id=case.id,
        domain="SALES",
        source_type="SALES_CASE",
        status="PENDING",
        extra={"submitted_by": body.submitted_by, "knowledge_item_id": item.id},
    )
    return case


# ============ 知识审核与列表（管理后台） ============


@admin_router.post("/knowledge/{item_id}/approve", response_model=GenericMessageResponse, dependencies=[Depends(require_admin)])
def approve_knowledge(
    item_id: int,
    body: KnowledgeApproveRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    if item.status == KnowledgeStatus.APPROVED:
        return GenericMessageResponse(message="Item already approved")

    item.approve(approved_by=body.approved_by)
    db.commit()
    db.refresh(item)

    structured_log(
        event="knowledge_approved",
        item_id=item.id,
        domain=item.domain.value,
        source_type=item.source_type.value,
        status="APPROVED",
        extra={"approved_by": body.approved_by},
    )

    # 不传请求级 db：后台线程跨请求使用同一 Session 非线程安全，
    # services 层在 db=None 时自建独立 session（见 sync_approved_knowledge）
    background_tasks.add_task(sync_approved_knowledge, item.id)
    return GenericMessageResponse(message="Approved and sync scheduled")


@admin_router.post("/knowledge/{item_id}/reject", response_model=GenericMessageResponse, dependencies=[Depends(require_admin)])
def reject_knowledge(
    item_id: int,
    body: KnowledgeRejectRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """驳回知识项（PENDING/DRAFT -> REJECTED）。若已同步过，异步删除 Dify 文档。"""
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    if item.status == KnowledgeStatus.REJECTED:
        return GenericMessageResponse(message="Item already rejected")

    item.reject(rejected_by=body.operator)
    db.commit()
    db.refresh(item)

    structured_log(
        event="knowledge_rejected",
        item_id=item.id,
        domain=item.domain.value,
        source_type=item.source_type.value,
        status="REJECTED",
        extra={"operator": body.operator, "vector_doc_id": item.vector_doc_id},
    )

    if item.vector_doc_id:
        background_tasks.add_task(sync_removed_knowledge, item.id)
    return GenericMessageResponse(message="Rejected")


@admin_router.post("/knowledge/{item_id}/revoke", response_model=GenericMessageResponse, dependencies=[Depends(require_admin)])
def revoke_knowledge(
    item_id: int,
    body: KnowledgeRejectRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """下线已上线知识（APPROVED -> REVOKED），异步删除 Dify 文档并清向量引用。"""
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    if item.status != KnowledgeStatus.APPROVED:
        raise HTTPException(status_code=400, detail="Only APPROVED items can be revoked")

    item.revoke(revoked_by=body.operator)
    db.commit()
    db.refresh(item)

    structured_log(
        event="knowledge_revoked",
        item_id=item.id,
        domain=item.domain.value,
        source_type=item.source_type.value,
        status="REVOKED",
        extra={"operator": body.operator, "vector_doc_id": item.vector_doc_id},
    )

    if item.vector_doc_id:
        background_tasks.add_task(sync_removed_knowledge, item.id)
    return GenericMessageResponse(message="Revoked, doc deletion scheduled")


@admin_router.get("/knowledge", response_model=KnowledgeListResponse, dependencies=[Depends(require_admin)])
def list_knowledge(
    domain: str = Query(None, pattern="^(CS|SALES|DOCTOR)$"),
    status: str = Query(None, pattern="^(DRAFT|PENDING|APPROVED|REJECTED|REVOKED)$"),
    category: str = Query(None, min_length=1, max_length=64),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(KnowledgeItem)
    if domain:
        query = query.filter(KnowledgeItem.domain == domain)
    if status:
        query = query.filter(KnowledgeItem.status == status)
    if category:
        query = query.filter(KnowledgeItem.category == category)
    total = query.count()
    items = query.order_by(KnowledgeItem.created_at.desc()).offset(skip).limit(limit).all()
    return KnowledgeListResponse(total=total, items=[KnowledgeItemOut.model_validate(i) for i in items])


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
    knowledge_item_id: int = None,
    db: Session = Depends(get_db),
):
    record = db.query(UnansweredQuestion).filter(UnansweredQuestion.id == question_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Question not found")
    record.resolve(knowledge_item_id=knowledge_item_id)
    db.commit()
    return GenericMessageResponse(message="Resolved")
