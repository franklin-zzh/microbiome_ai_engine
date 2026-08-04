from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CsChatLogCreate(BaseModel):
    """新增一条对话日志"""
    channel: str = Field(..., pattern="^(WXKF|MP|H5|WECOM_GROUP)$")
    session_id: str = Field(..., min_length=1, max_length=128)
    open_id: str = Field(..., min_length=1, max_length=128)
    user_message: str = Field(..., min_length=1)
    ai_reply: Optional[str] = None
    retrieved_chunks: Optional[List[Dict[str, Any]]] = None
    intent: Optional[str] = None
    match_score: Optional[float] = Field(None, ge=0, le=1)
    satisfaction: Optional[int] = Field(None, ge=1, le=5)
    hit_human: bool = False
    human_takeover: Optional[str] = None
    risk_flag: bool = False
    meta: Optional[Dict[str, Any]] = None


class CsChatLogOut(CsChatLogCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CsChatLogListResponse(BaseModel):
    total: int
    items: List[CsChatLogOut]


class SessionStateOut(BaseModel):
    session_id: str
    channel: str
    open_id: str
    state: str
    negative_streak: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
