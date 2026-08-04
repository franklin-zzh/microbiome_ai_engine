import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class KnowledgeDomain(str, enum.Enum):
    CS = "CS"
    SALES = "SALES"
    DOCTOR = "DOCTOR"


class KnowledgeSourceType(str, enum.Enum):
    MANUAL = "MANUAL"
    CS_GAP = "CS_GAP"
    SALES_CASE = "SALES_CASE"


class KnowledgeStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class UnansweredStatus(str, enum.Enum):
    OPEN = "OPEN"
    PROCESSING = "PROCESSING"
    RESOLVED = "RESOLVED"
    IGNORED = "IGNORED"


class SalesCaseStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class KnowledgeItem(Base):
    """统一知识库 / 话术库（mb_ai_engine.core_knowledge_items）"""
    __tablename__ = "core_knowledge_items"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(Enum(KnowledgeDomain, name="knowledge_domain"), nullable=False, default=KnowledgeDomain.CS)
    source_type = Column(Enum(KnowledgeSourceType, name="knowledge_source_type"), nullable=False, default=KnowledgeSourceType.MANUAL)
    status = Column(Enum(KnowledgeStatus, name="knowledge_status"), nullable=False, default=KnowledgeStatus.PENDING)
    # 主分类（强规范枚举/路径，如 product.probiotics），与 tags（扁平标签）职责分离，支持索引筛选
    category = Column(String(64), nullable=False, default="GENERAL", server_default="GENERAL", index=True)

    title = Column(String(255), nullable=False)
    question = Column(Text)
    answer = Column(Text, nullable=False)
    tags = Column(JSON, default=list)

    cs_gap_id = Column(Integer, ForeignKey("core_unanswered_questions.id", ondelete="SET NULL"))
    sales_case_id = Column(Integer, ForeignKey("core_sales_cases.id", ondelete="SET NULL"))

    vector_doc_id = Column(String(255))

    created_by = Column(String(128))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    approved_at = Column(DateTime)
    approved_by = Column(String(128))

    cs_gap = relationship("UnansweredQuestion", foreign_keys=[cs_gap_id])
    sales_case = relationship("SalesCase", foreign_keys=[sales_case_id])

    def approve(self, approved_by: str) -> None:
        self.status = KnowledgeStatus.APPROVED
        self.approved_by = approved_by
        self.approved_at = datetime.now()


class UnansweredQuestion(Base):
    """客服未解答问题捕获（mb_ai_engine.core_unanswered_questions）"""
    __tablename__ = "core_unanswered_questions"

    id = Column(Integer, primary_key=True, index=True)
    user_query = Column(Text, nullable=False)
    normalized_query = Column(Text)
    context = Column(JSON)
    match_score = Column(String(10))  # 保留小数位文本，避免精度问题
    status = Column(Enum(UnansweredStatus, name="unanswered_status"), nullable=False, default=UnansweredStatus.OPEN)

    knowledge_item_id = Column(Integer, ForeignKey("core_knowledge_items.id", ondelete="SET NULL"))

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    resolved_at = Column(DateTime)

    def resolve(self, knowledge_item_id: Optional[int] = None) -> None:
        self.status = UnansweredStatus.RESOLVED
        self.resolved_at = datetime.now()
        if knowledge_item_id:
            self.knowledge_item_id = knowledge_item_id


class SalesCase(Base):
    """销售实战案例（mb_ai_engine.core_sales_cases）"""
    __tablename__ = "core_sales_cases"

    id = Column(Integer, primary_key=True, index=True)
    submitted_by = Column(String(128), nullable=False)
    raw_chat_log = Column(Text, nullable=False)

    customer_type = Column(String(255))
    core_objection = Column(Text)
    breakthrough_logic = Column(Text)
    follow_up_script = Column(Text)
    extracted_summary = Column(JSON)

    status = Column(Enum(SalesCaseStatus, name="sales_case_status"), nullable=False, default=SalesCaseStatus.PENDING)
    knowledge_item_id = Column(Integer, ForeignKey("core_knowledge_items.id", ondelete="SET NULL"))

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    approved_at = Column(DateTime)
    approved_by = Column(String(128))

    def approve(self, approved_by: str) -> None:
        self.status = SalesCaseStatus.APPROVED
        self.approved_by = approved_by
        self.approved_at = datetime.now()
