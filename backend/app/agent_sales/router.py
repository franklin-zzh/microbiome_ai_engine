"""销售 Agent 业务域：REST 路由

保留历史 URL /chat/leads（前端已接入），路由归属迁移至销售域。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.agent_sales.models import LeadsPreview
from app.agent_sales.schemas import LeadsPreviewOut
from app.core.database import get_db
from app.core.security import require_admin

router = APIRouter(prefix="/chat", tags=["sales"])


@router.get("/leads", response_model=list[LeadsPreviewOut], dependencies=[Depends(require_admin)])
def list_leads(
    status: str = Query(None, pattern="^(NEW|ASSIGNED|CONVERTED|CLOSED)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(LeadsPreview)
    if status:
        query = query.filter(LeadsPreview.status == status)
    return query.order_by(LeadsPreview.created_at.desc()).offset(skip).limit(limit).all()
