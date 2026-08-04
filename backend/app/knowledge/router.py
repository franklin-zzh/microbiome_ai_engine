"""知识库域（横切共享，mb_ai_core）：REST 路由

- router（/knowledge）：知识项提交 / 销售案例提交
- admin_router（/admin）：知识审核与列表
- cs_router（/cs）：未解答问题捕获（客服缺口反哺知识库，URL 前缀兼容旧版）
"""
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import structured_log
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
    SalesCaseOut,
    SalesCaseSubmitRequest,
    UnansweredCaptureRequest,
    UnansweredQuestionOut,
)
from app.knowledge.services import sync_approved_knowledge

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])
cs_router = APIRouter(prefix="/cs", tags=["cs"])


# ============ 知识项提交 ============


@router.post("/submit", response_model=KnowledgeItemOut)
def submit_knowledge(
    body: KnowledgeItemCreate,
    db: Session = Depends(get_db),
):
    item = KnowledgeItem(
        domain=body.domain,
        source_type=body.source_type,
        status=KnowledgeStatus.PENDING,
        title=body.title,
        question=body.question,
        answer=body.answer,
        tags=body.tags or [],
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


@router.post("/sales-case", response_model=SalesCaseOut)
def submit_sales_case(
    body: SalesCaseSubmitRequest,
    db: Session = Depends(get_db),
):
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


@admin_router.post("/knowledge/{item_id}/approve", response_model=GenericMessageResponse)
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

    background_tasks.add_task(sync_approved_knowledge, item.id, db)
    return GenericMessageResponse(message="Approved and sync scheduled")


@admin_router.get("/knowledge", response_model=KnowledgeListResponse)
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


# ============ 未解答问题捕获（客服缺口反哺） ============


@cs_router.post("/unanswered/capture", response_model=GenericMessageResponse)
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


@cs_router.get("/unanswered", response_model=list[UnansweredQuestionOut])
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


@cs_router.post("/unanswered/{question_id}/resolve", response_model=GenericMessageResponse)
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
