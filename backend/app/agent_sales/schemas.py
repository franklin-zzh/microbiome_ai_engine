"""销售 Agent 业务域：线索输出 schema"""
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class LeadsPreviewOut(BaseModel):
    id: int
    session_id: str
    open_id: str
    channel: str
    intent_tags: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    assigned_to: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
