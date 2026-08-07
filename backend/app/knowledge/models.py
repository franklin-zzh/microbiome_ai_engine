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
    REVOKED = "REVOKED"


class SyncStatus(str, enum.Enum):
    """知识项与 Dify 的同步状态（技术状态，与审核状态 status 分离）

    流转：NOT_SYNCED -> QUEUED -> INDEXING -> COMPLETED | FAILED
    reject/revoke 时：COMPLETED -> DELETING -> NOT_SYNCED（清 vector_doc_id）
    """
    NOT_SYNCED = "NOT_SYNCED"
    QUEUED = "QUEUED"
    INDEXING = "INDEXING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DELETING = "DELETING"


class SyncTaskAction(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class SyncTaskStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


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
    # ---- 同步状态（与 status 审核状态分离：status 由人决定，sync_status 由系统决定）----
    sync_status = Column(
        Enum(SyncStatus, name="sync_status"),
        nullable=False,
        default=SyncStatus.NOT_SYNCED,
        server_default=SyncStatus.NOT_SYNCED.value,
    )
    sync_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    last_sync_error = Column(Text)
    # 原始文档（上传入审）相对存储根目录的路径；纯文本知识项为 NULL
    source_file = Column(String(512))

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

    def reject(self, rejected_by: str) -> None:
        self.status = KnowledgeStatus.REJECTED
        self.approved_by = rejected_by
        self.approved_at = datetime.now()

    def revoke(self, revoked_by: str) -> None:
        self.status = KnowledgeStatus.REVOKED
        self.approved_by = revoked_by
        self.approved_at = datetime.now()


class SyncTask(Base):
    """Dify 同步对账任务（outbox：审核/驳回动作 -> 异步落 Dify）

    - 由审核/驳回/下线接口注册（QUEUED），后台 worker 取出执行；
    - 失败保留 FAILED + next_retry_at，支持重试（幂等）；
    - 每知识项最多一条进行中的任务（service 层按 item_id+status 去重）。
    """
    __tablename__ = "core_sync_tasks"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("core_knowledge_items.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(Enum(SyncTaskAction, name="sync_task_action"), nullable=False, default=SyncTaskAction.CREATE)
    status = Column(
        Enum(SyncTaskStatus, name="sync_task_status"),
        nullable=False,
        default=SyncTaskStatus.QUEUED,
        server_default=SyncTaskStatus.QUEUED.value,
    )
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=5, server_default="5")
    next_retry_at = Column(DateTime)
    last_error = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    item = relationship("KnowledgeItem", foreign_keys=[item_id])


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
