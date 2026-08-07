from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeItemBase(BaseModel):
    domain: str = Field(..., pattern="^(CS|SALES|DOCTOR)$")
    title: str = Field(..., min_length=1, max_length=255)
    question: Optional[str] = None
    answer: str = Field(..., min_length=1)
    tags: Optional[List[str]] = Field(default_factory=list)
    # 主分类（强规范枚举/路径，如 product.probiotics），默认 GENERAL；与 tags 扁平标签职责分离
    category: str = Field(default="GENERAL", max_length=64)
    # 原始文档相对存储根路径（上传入审用，纯文本知识项为 None）
    source_file: Optional[str] = None


class KnowledgeItemCreate(KnowledgeItemBase):
    source_type: str = Field(default="MANUAL", pattern="^(MANUAL|CS_GAP|SALES_CASE)$")
    cs_gap_id: Optional[int] = None
    sales_case_id: Optional[int] = None
    created_by: Optional[str] = None


class KnowledgeItemOut(KnowledgeItemBase):
    id: int
    source_type: str
    status: str
    vector_doc_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class KnowledgeListResponse(BaseModel):
    total: int
    items: List[KnowledgeItemOut]


class KnowledgeApproveRequest(BaseModel):
    approved_by: str = Field(..., min_length=1)


class KnowledgeRejectRequest(BaseModel):
    """驳回 / 下线共用的操作人字段"""
    operator: str = Field(..., min_length=1)


class UnansweredCaptureRequest(BaseModel):
    user_query: str = Field(..., min_length=1)
    normalized_query: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    match_score: Optional[float] = None
    risk_flag: Optional[bool] = False


class UnansweredQuestionOut(BaseModel):
    id: int
    user_query: str
    status: str
    match_score: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SalesCaseSubmitRequest(BaseModel):
    submitted_by: str = Field(..., min_length=1)
    raw_chat_log: str = Field(..., min_length=1)
    customer_type: Optional[str] = None
    core_objection: Optional[str] = None
    breakthrough_logic: Optional[str] = None
    follow_up_script: Optional[str] = None
    extracted_summary: Optional[Dict[str, Any]] = None


class SalesCaseOut(BaseModel):
    id: int
    submitted_by: str
    status: str
    customer_type: Optional[str] = None
    core_objection: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GenericMessageResponse(BaseModel):
    message: str
