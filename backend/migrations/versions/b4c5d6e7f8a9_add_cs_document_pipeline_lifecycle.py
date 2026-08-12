"""add_cs_document_pipeline_lifecycle

Revision ID: b4c5d6e7f8a9
Revises: a1b2c3d4e5f6
Create Date: 2026-08-12 09:00:00.000000

CS 原始文档通过 Dify Knowledge Pipeline 入库时的事实源：
asset -> logical document -> immutable version -> Dify publish target -> block snapshot.
存量 core_knowledge_items 不删除，继续兼容销售与历史手工 Q&A。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "core_knowledge_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_provider", sa.String(length=32), nullable=False, server_default="LOCAL"),
        sa.Column("bucket", sa.String(length=128), nullable=True),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
        mysql_charset="utf8mb4",
    )
    op.create_index(op.f("ix_core_knowledge_assets_id"), "core_knowledge_assets", ["id"], unique=False)
    op.create_index(op.f("ix_core_knowledge_assets_sha256"), "core_knowledge_assets", ["sha256"], unique=False)

    op.create_table(
        "core_knowledge_documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("domain", sa.Enum("CS", "SALES", "DOCTOR", name="knowledge_document_domain"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="GENERAL"),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("status", sa.Enum("ACTIVE", "ARCHIVED", name="knowledge_document_status"), nullable=False, server_default="ACTIVE"),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
    )
    op.create_index(op.f("ix_core_knowledge_documents_id"), "core_knowledge_documents", ["id"], unique=False)
    op.create_index(op.f("ix_core_knowledge_documents_category"), "core_knowledge_documents", ["category"], unique=False)

    op.create_table(
        "core_knowledge_document_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_snapshot", sa.JSON(), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "PENDING", "APPROVED", "PUBLISHED", "SUPERSEDED", "REJECTED", "REVOKED", name="knowledge_version_status"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["core_knowledge_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["core_knowledge_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "revision", name="uq_knowledge_document_revision"),
        mysql_charset="utf8mb4",
    )
    op.create_index(op.f("ix_core_knowledge_document_versions_id"), "core_knowledge_document_versions", ["id"], unique=False)
    op.create_index(op.f("ix_core_knowledge_document_versions_document_id"), "core_knowledge_document_versions", ["document_id"], unique=False)
    op.create_index(op.f("ix_core_knowledge_document_versions_content_sha256"), "core_knowledge_document_versions", ["content_sha256"], unique=False)
    op.create_index(op.f("ix_core_knowledge_document_versions_status"), "core_knowledge_document_versions", ["status"], unique=False)

    op.create_table(
        "core_dify_publish_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.String(length=255), nullable=False),
        sa.Column("pipeline_start_node_id", sa.String(length=128), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("dify_file_id", sa.String(length=255), nullable=True),
        sa.Column("pipeline_run_id", sa.String(length=255), nullable=True),
        sa.Column("dify_document_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum("NOT_SYNCED", "QUEUED", "RUNNING", "INDEXING", "COMPLETED", "FAILED", "DELETE_PENDING", "DELETED", name="dify_publish_status"),
            nullable=False,
            server_default="NOT_SYNCED",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("run_output", sa.JSON(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["version_id"], ["core_knowledge_document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
    )
    op.create_index(op.f("ix_core_dify_publish_targets_id"), "core_dify_publish_targets", ["id"], unique=False)
    op.create_index(op.f("ix_core_dify_publish_targets_version_id"), "core_dify_publish_targets", ["version_id"], unique=False)
    op.create_index(op.f("ix_core_dify_publish_targets_status"), "core_dify_publish_targets", ["status"], unique=False)

    op.create_table(
        "core_dify_document_blocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("publish_target_id", sa.Integer(), nullable=False),
        sa.Column("dify_segment_id", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("block_type", sa.Enum("TEXT", "QA", name="knowledge_block_type"), nullable=False, server_default="TEXT"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["publish_target_id"], ["core_dify_publish_targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("publish_target_id", "dify_segment_id", name="uq_dify_target_segment"),
        mysql_charset="utf8mb4",
    )
    op.create_index(op.f("ix_core_dify_document_blocks_id"), "core_dify_document_blocks", ["id"], unique=False)
    op.create_index(op.f("ix_core_dify_document_blocks_publish_target_id"), "core_dify_document_blocks", ["publish_target_id"], unique=False)
    op.create_index(op.f("ix_core_dify_document_blocks_content_sha256"), "core_dify_document_blocks", ["content_sha256"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_core_dify_document_blocks_content_sha256"), table_name="core_dify_document_blocks")
    op.drop_index(op.f("ix_core_dify_document_blocks_publish_target_id"), table_name="core_dify_document_blocks")
    op.drop_index(op.f("ix_core_dify_document_blocks_id"), table_name="core_dify_document_blocks")
    op.drop_table("core_dify_document_blocks")
    op.drop_index(op.f("ix_core_dify_publish_targets_status"), table_name="core_dify_publish_targets")
    op.drop_index(op.f("ix_core_dify_publish_targets_version_id"), table_name="core_dify_publish_targets")
    op.drop_index(op.f("ix_core_dify_publish_targets_id"), table_name="core_dify_publish_targets")
    op.drop_table("core_dify_publish_targets")
    op.drop_index(op.f("ix_core_knowledge_document_versions_status"), table_name="core_knowledge_document_versions")
    op.drop_index(op.f("ix_core_knowledge_document_versions_content_sha256"), table_name="core_knowledge_document_versions")
    op.drop_index(op.f("ix_core_knowledge_document_versions_document_id"), table_name="core_knowledge_document_versions")
    op.drop_index(op.f("ix_core_knowledge_document_versions_id"), table_name="core_knowledge_document_versions")
    op.drop_table("core_knowledge_document_versions")
    op.drop_index(op.f("ix_core_knowledge_documents_category"), table_name="core_knowledge_documents")
    op.drop_index(op.f("ix_core_knowledge_documents_id"), table_name="core_knowledge_documents")
    op.drop_table("core_knowledge_documents")
    op.drop_index(op.f("ix_core_knowledge_assets_sha256"), table_name="core_knowledge_assets")
    op.drop_index(op.f("ix_core_knowledge_assets_id"), table_name="core_knowledge_assets")
    op.drop_table("core_knowledge_assets")
