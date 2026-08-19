from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


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


# ============ CS 文档知识库（Pipeline 入库） ============


class KnowledgeAssetOut(BaseModel):
    id: int
    original_filename: str
    source_type: str = "UPLOAD"
    source_domain: Optional[str] = None
    source_url: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: int
    sha256: str
    storage_provider: str
    object_key: str
    extra_meta: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeDocumentCreate(BaseModel):
    """创建逻辑文档及其 V1.0 内容版本。"""

    domain: str = Field(default="CS", pattern="^CS$")
    title: str = Field(..., min_length=1, max_length=255)
    asset_id: int = Field(..., gt=0)
    category: str = Field(default="GENERAL", max_length=64)
    tags: List[str] = Field(default_factory=list)
    # 知识库形态单选：qa_model → 问答库（Pipeline 生成 QA）；text_model → 长文档库（原生切割）
    doc_form: str = Field(default="qa_model", pattern="^(qa_model|text_model|hierarchical_model)$")
    # 为空时回退 settings.cs_pipeline_version（.env CS_PIPELINE_VERSION）
    pipeline_version: Optional[str] = Field(default=None, max_length=64)
    created_by: str = Field(..., min_length=1, max_length=128)


class KnowledgeDocumentVersionCreate(BaseModel):
    """从已有逻辑文档发起更新；服务端自动计算下一 revision。"""

    asset_id: int = Field(..., gt=0)
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    category: Optional[str] = Field(default=None, max_length=64)
    tags: Optional[List[str]] = None
    pipeline_version: Optional[str] = Field(default=None, max_length=64)
    created_by: str = Field(..., min_length=1, max_length=128)


class KnowledgeDocumentReviewRequest(BaseModel):
    operator: str = Field(..., min_length=1, max_length=128)
    review_note: Optional[str] = Field(default=None, max_length=4000)


class DifyBlockOut(BaseModel):
    id: int
    dify_segment_id: str
    position: Optional[int] = None
    block_type: str
    content: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    content_sha256: str

    model_config = ConfigDict(from_attributes=True)


class DifyPublishTargetOut(BaseModel):
    id: int
    dataset_id: str
    # qa_model 走 Pipeline 时有 start node；create_by_file 直传路径为 None
    pipeline_start_node_id: Optional[str] = None
    pipeline_version: str
    dify_file_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    dify_document_id: Optional[str] = None
    status: str
    attempts: int
    last_error: Optional[str] = None
    indexed_at: Optional[datetime] = None
    blocks: List[DifyBlockOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class KnowledgeDocumentVersionOut(BaseModel):
    id: int
    document_id: int
    revision: int
    asset_id: int
    content_sha256: str
    content_snapshot: Dict[str, Any]
    pipeline_version: str
    status: str
    review_note: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: datetime
    publish_targets: List[DifyPublishTargetOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class KnowledgeDocumentOut(BaseModel):
    id: int
    domain: str
    title: str
    category: str
    doc_form: str
    tags: List[str] = Field(default_factory=list)
    status: str
    current_version_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    versions: List[KnowledgeDocumentVersionOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class BatchReviewRequest(BaseModel):
    version_ids: List[int] = Field(..., min_length=1)
    operator: str = Field(default="admin", min_length=1)
    review_note: Optional[str] = None


class AssetContentOut(BaseModel):
    asset_id: int
    filename: str
    content: str
    source_url: Optional[str] = None
    source_domain: Optional[str] = None
    source_type: Optional[str] = None
    size_bytes: int



class KnowledgeDocumentListResponse(BaseModel):
    total: int
    items: List[KnowledgeDocumentOut]
