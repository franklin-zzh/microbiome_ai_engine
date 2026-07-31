from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.logging import structured_log
from app.models.knowledge import KnowledgeItem, KnowledgeStatus
from app.schemas.knowledge import (
    GenericMessageResponse,
    KnowledgeApproveRequest,
    KnowledgeItemOut,
    KnowledgeListResponse,
)
from app.services import dify_sync

router = APIRouter(prefix="/admin", tags=["admin"])


def _do_sync(item_id: int, db: Session = None) -> None:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        item = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()
        if not item or item.status != KnowledgeStatus.APPROVED:
            return
        doc_id = dify_sync.sync_knowledge_to_dify(
            item_id=item.id,
            domain=item.domain.value,
            title=item.title,
            question=item.question,
            answer=item.answer,
            tags=item.tags,
            vector_doc_id=item.vector_doc_id,
        )
        if doc_id and doc_id != item.vector_doc_id:
            item.vector_doc_id = doc_id
            db.commit()
    except dify_sync.DifySyncError:
        # 失败已记录结构化日志，状态保持 APPROVED，便于后续重试
        pass
    finally:
        if close_db:
            db.close()


@router.post("/knowledge/{item_id}/approve", response_model=GenericMessageResponse)
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

    background_tasks.add_task(_do_sync, item.id, db)
    return GenericMessageResponse(message="Approved and sync scheduled")


@router.get("/knowledge", response_model=KnowledgeListResponse)
def list_knowledge(
    domain: str = Query(None, pattern="^(CS|SALES|DOCTOR)$"),
    status: str = Query(None, pattern="^(DRAFT|PENDING|APPROVED|REJECTED)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(KnowledgeItem)
    if domain:
        query = query.filter(KnowledgeItem.domain == domain)
    if status:
        query = query.filter(KnowledgeItem.status == status)
    total = query.count()
    items = query.order_by(KnowledgeItem.created_at.desc()).offset(skip).limit(limit).all()
    return KnowledgeListResponse(total=total, items=[KnowledgeItemOut.model_validate(i) for i in items])
