from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import structured_log
from app.models.knowledge import (
    KnowledgeItem,
    KnowledgeSourceType,
    KnowledgeStatus,
    SalesCase,
    SalesCaseStatus,
)
from app.schemas.knowledge import (
    GenericMessageResponse,
    KnowledgeItemCreate,
    KnowledgeItemOut,
    SalesCaseOut,
    SalesCaseSubmitRequest,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


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
