"""add_doc_form_and_direct_publish

Revision ID: d1e2f3a4b5c6
Revises: c9d8e7f6a5b4
Create Date: 2026-08-12 17:00:00.000000

DOC 知识库（非 QA 文件上传 + Dify 原生切割）：
- core_knowledge_documents 增加 doc_form（文档级「知识库形态」单选）：
  qa_model -> CS_QA_DATASET_ID（发布走 Knowledge Pipeline）
  text_model / hierarchical_model -> CS_DOC_DATASET_ID（发布走 create_by_file 直传）
  一个文档一个形态一个目标库，绝不双写。
- core_dify_publish_targets.pipeline_start_node_id 改 nullable：create_by_file 直传路径
  没有 Pipeline start node。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c9d8e7f6a5b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE core_knowledge_documents "
        "ADD COLUMN doc_form ENUM('qa_model','text_model','hierarchical_model') "
        "NOT NULL DEFAULT 'qa_model' AFTER category"
    )
    op.create_index(op.f("ix_core_knowledge_documents_doc_form"), "core_knowledge_documents", ["doc_form"], unique=False)
    op.alter_column(
        "core_dify_publish_targets",
        "pipeline_start_node_id",
        existing_type=sa.String(length=128),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "core_dify_publish_targets",
        "pipeline_start_node_id",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.drop_index(op.f("ix_core_knowledge_documents_doc_form"), table_name="core_knowledge_documents")
    op.execute("ALTER TABLE core_knowledge_documents DROP COLUMN doc_form")
