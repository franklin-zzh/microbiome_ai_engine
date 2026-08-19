import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class KnowledgeDomain(str, enum.Enum):
    CS = "CS"
    SALES = "SALES"
    DOCTOR = "DOCTOR"


# ============ CS 文档知识库：事实源、版本和 Dify Pipeline 投影 ============


class KnowledgeDocumentStatus(str, enum.Enum):
    """逻辑文档的生命周期状态。

    一个逻辑文档（例如《检测报告 FAQ》）只有一个当前线上版本；内容修改会创建
    新版本，而不是覆盖旧记录。状态由版本状态派生，便于列表检索。
    """

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class KnowledgeVersionStatus(str, enum.Enum):
    """内容版本生命周期；审核状态和 Dify 发布状态不再混用。"""

    DRAFT = "DRAFT"
    PENDING = "PENDING"
    APPROVED = "APPROVED"       # 人工审核通过，等待/正在发布
    PUBLISHED = "PUBLISHED"     # Dify Pipeline 成功且索引完成
    SUPERSEDED = "SUPERSEDED"   # 被后一已发布版本替代，原记录仍保留
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class PublishStatus(str, enum.Enum):
    """Dify 侧的技术状态；它不是人工审核结论。"""

    NOT_SYNCED = "NOT_SYNCED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    INDEXING = "INDEXING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DELETE_PENDING = "DELETE_PENDING"
    DELETED = "DELETED"


class KnowledgeBlockType(str, enum.Enum):
    TEXT = "TEXT"
    QA = "QA"


class KnowledgeDocForm(str, enum.Enum):
    """文档级「知识库形态」单选（对齐 Dify 契约 §2 的 doc_form 枚举）。

    一个文档 = 一种形态 = 一个目标知识库，绝不双写：
    - qa_model → CS_QA_DATASET_ID（发布走 Knowledge Pipeline，生成 QA 对）
    - text_model / hierarchical_model → CS_DOC_DATASET_ID（发布走 create_by_file 直传，Dify 原生切割）
    """

    QA_MODEL = "qa_model"
    TEXT_MODEL = "text_model"
    HIERARCHICAL_MODEL = "hierarchical_model"


class KnowledgeAsset(Base):
    """原始上传文件的不可变元数据。

    文件二进制不落 MySQL。MVP 使用受保护的本地对象路径；将来迁移至 MinIO/OSS
    时只需替换 storage_provider/bucket/object_key 的存储适配器。
    """

    __tablename__ = "core_knowledge_assets"

    id = Column(Integer, primary_key=True, index=True)
    original_filename = Column(String(255), nullable=False)
    source_type = Column(String(32), nullable=False, default="UPLOAD", server_default="UPLOAD", index=True)  # UPLOAD / CRAWLER / ETL
    source_domain = Column(String(128), index=True)  # 例如 "fmtbio.com"
    source_url = Column(String(1024))                # 例如 "https://fmtbio.com/hangye/index_10.html"
    mime_type = Column(String(128))
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    storage_provider = Column(String(32), nullable=False, default="LOCAL")
    bucket = Column(String(128))
    object_key = Column(String(512), nullable=False, unique=True)
    extra_meta = Column(JSON, default=dict)          # 存储 depth, relative_path, seed_url, crawled_at 等
    created_by = Column(String(128))
    created_at = Column(DateTime, server_default=func.now())


class KnowledgeDocument(Base):
    """稳定的逻辑文档标识，不随文件名、正文或 Dify document id 改变。"""

    __tablename__ = "core_knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(Enum(KnowledgeDomain, name="knowledge_document_domain"), nullable=False, default=KnowledgeDomain.CS)
    title = Column(String(255), nullable=False)
    category = Column(String(64), nullable=False, default="GENERAL", server_default="GENERAL", index=True)
    # 知识库形态（qa_model/text_model/hierarchical_model），发布时决定目标 dataset 与发布路径
    doc_form = Column(
        Enum(
            KnowledgeDocForm,
            name="knowledge_document_doc_form",
            # 存储用小写枚举值（qa_model/text_model/hierarchical_model），与 Dify doc_form 契约一致
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=KnowledgeDocForm.QA_MODEL,
        server_default=KnowledgeDocForm.QA_MODEL.value,
        index=True,
    )
    tags = Column(JSON, default=list)
    status = Column(
        Enum(KnowledgeDocumentStatus, name="knowledge_document_status"),
        nullable=False,
        default=KnowledgeDocumentStatus.ACTIVE,
        server_default=KnowledgeDocumentStatus.ACTIVE.value,
    )
    current_version_id = Column(Integer)  # 逻辑关联，避免 document/version 的循环外键
    created_by = Column(String(128))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    versions = relationship(
        "KnowledgeDocumentVersion",
        back_populates="document",
        foreign_keys="KnowledgeDocumentVersion.document_id",
        order_by="KnowledgeDocumentVersion.revision.asc()",
    )


class KnowledgeDocumentVersion(Base):
    """一次待审内容快照；revision 自动递增，显示层可渲染成 V1.0、V1.1。"""

    __tablename__ = "core_knowledge_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "revision", name="uq_knowledge_document_revision"),)

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("core_knowledge_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    asset_id = Column(Integer, ForeignKey("core_knowledge_assets.id", ondelete="RESTRICT"), nullable=False)
    content_sha256 = Column(String(64), nullable=False, index=True)
    # 保存提交时的标题/分类/标签，保证历史审核视图不被逻辑文档后续编辑改写。
    content_snapshot = Column(JSON, nullable=False, default=dict)
    pipeline_version = Column(String(64), nullable=False, default="default")
    status = Column(
        Enum(KnowledgeVersionStatus, name="knowledge_version_status"),
        nullable=False,
        default=KnowledgeVersionStatus.PENDING,
        server_default=KnowledgeVersionStatus.PENDING.value,
        index=True,
    )
    review_note = Column(Text)
    reviewed_by = Column(String(128))
    reviewed_at = Column(DateTime)
    created_by = Column(String(128))
    created_at = Column(DateTime, server_default=func.now())

    document = relationship("KnowledgeDocument", back_populates="versions", foreign_keys=[document_id])
    asset = relationship("KnowledgeAsset", foreign_keys=[asset_id])
    publish_targets = relationship(
        "DifyPublishTarget",
        back_populates="version",
        foreign_keys="DifyPublishTarget.version_id",
        order_by="DifyPublishTarget.id.desc()",
    )


class DifyPublishTarget(Base):
    """一个内容版本到 Dify Dataset/Pipeline 的可重建投影和运行审计。"""

    __tablename__ = "core_dify_publish_targets"

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, ForeignKey("core_knowledge_document_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_id = Column(String(255), nullable=False)
    # qa_model 走 Pipeline 时需要 start node；create_by_file 直传路径无此节点，可为空
    pipeline_start_node_id = Column(String(128))
    pipeline_version = Column(String(64), nullable=False, default="default")
    dify_file_id = Column(String(255))
    pipeline_run_id = Column(String(255))
    dify_document_id = Column(String(255))
    status = Column(
        Enum(PublishStatus, name="dify_publish_status"),
        nullable=False,
        default=PublishStatus.NOT_SYNCED,
        server_default=PublishStatus.NOT_SYNCED.value,
        index=True,
    )
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    last_error = Column(Text)
    run_output = Column(JSON)
    indexed_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    version = relationship("KnowledgeDocumentVersion", back_populates="publish_targets", foreign_keys=[version_id])
    blocks = relationship(
        "DifyDocumentBlock",
        back_populates="publish_target",
        foreign_keys="DifyDocumentBlock.publish_target_id",
        order_by="DifyDocumentBlock.position.asc()",
    )


class DifyDocumentBlock(Base):
    """Pipeline 生成后从 Dify 回读的块快照，用于版本差异和可追溯审核。"""

    __tablename__ = "core_dify_document_blocks"
    __table_args__ = (UniqueConstraint("publish_target_id", "dify_segment_id", name="uq_dify_target_segment"),)

    id = Column(Integer, primary_key=True, index=True)
    publish_target_id = Column(Integer, ForeignKey("core_dify_publish_targets.id", ondelete="CASCADE"), nullable=False, index=True)
    dify_segment_id = Column(String(255), nullable=False)
    position = Column(Integer)
    block_type = Column(Enum(KnowledgeBlockType, name="knowledge_block_type"), nullable=False, default=KnowledgeBlockType.TEXT)
    content = Column(Text)
    question = Column(Text)
    answer = Column(Text)
    content_sha256 = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())

    publish_target = relationship("DifyPublishTarget", back_populates="blocks", foreign_keys=[publish_target_id])


class UnansweredStatus(str, enum.Enum):
    OPEN = "OPEN"
    PROCESSING = "PROCESSING"
    RESOLVED = "RESOLVED"
    IGNORED = "IGNORED"


class SalesCaseStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class UnansweredQuestion(Base):
    """客服未解答问题捕获（mb_ai_engine.core_unanswered_questions）"""
    __tablename__ = "core_unanswered_questions"

    id = Column(Integer, primary_key=True, index=True)
    user_query = Column(Text, nullable=False)
    normalized_query = Column(Text)
    context = Column(JSON)
    match_score = Column(String(10))  # 保留小数位文本，避免精度问题
    status = Column(Enum(UnansweredStatus, name="unanswered_status"), nullable=False, default=UnansweredStatus.OPEN)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    resolved_at = Column(DateTime)

    def resolve(self) -> None:
        self.status = UnansweredStatus.RESOLVED
        self.resolved_at = datetime.now()


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

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    approved_at = Column(DateTime)
    approved_by = Column(String(128))

    def approve(self, approved_by: str) -> None:
        self.status = SalesCaseStatus.APPROVED
        self.approved_by = approved_by
        self.approved_at = datetime.now()
