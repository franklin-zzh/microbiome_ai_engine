from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import structured_log
from app.models.knowledge import UnansweredQuestion, UnansweredStatus
from app.schemas.knowledge import (
    GenericMessageResponse,
    UnansweredCaptureRequest,
    UnansweredQuestionOut,
)

router = APIRouter(prefix="/cs", tags=["cs"])


@router.post("/unanswered/capture", response_model=GenericMessageResponse)
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


@router.get("/unanswered", response_model=list[UnansweredQuestionOut])
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


@router.post("/unanswered/{question_id}/resolve", response_model=GenericMessageResponse)
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
