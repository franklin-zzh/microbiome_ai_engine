"""drop_legacy_knowledge_item_chain

Revision ID: c9d8e7f6a5b4
Revises: b4c5d6e7f8a9
Create Date: 2026-08-12 16:00:00.000000

旧知识项链路下线（2026-08-12，数据不迁移直接删除）：
- core_unanswered_questions / core_sales_cases 移除指向 core_knowledge_items 的 knowledge_item_id 列
  （列上存在 MySQL 自动命名的 FK，先按 information_schema 查到约束名再 DROP FOREIGN KEY）
- drop core_knowledge_items / core_sync_tasks（手工录入 + 同步对账 outbox 全部下线）

新 CS 文档链路（core_knowledge_documents 等五表 + core_dify_publish_targets）不受影响。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, Sequence[str], None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fk_names(conn, table: str, column: str) -> list[str]:
    """查 information_schema 拿到 MySQL 自动命名的外键约束名（Base 未配置 naming_convention）。"""
    rows = conn.execute(
        sa.text(
            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    )
    return [str(r[0]) for r in rows if r[0]]


def _drop_column_with_fk(table: str, column: str) -> None:
    conn = op.get_bind()
    for name in _fk_names(conn, table, column):
        op.execute(f"ALTER TABLE {table} DROP FOREIGN KEY `{name}`")
    op.drop_column(table, column)


def upgrade() -> None:
    """Upgrade schema."""
    _drop_column_with_fk("core_unanswered_questions", "knowledge_item_id")
    _drop_column_with_fk("core_sales_cases", "knowledge_item_id")
    op.drop_table("core_sync_tasks")
    op.drop_table("core_knowledge_items")


def downgrade() -> None:
    """Downgrade schema（仅恢复表结构，不恢复数据）。"""
    op.create_table(
        "core_knowledge_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("domain", sa.Enum("CS", "SALES", "DOCTOR", name="knowledge_domain"), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum("MANUAL", "CS_GAP", "SALES_CASE", name="knowledge_source_type"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "PENDING", "APPROVED", "REJECTED", "REVOKED", name="knowledge_status"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="GENERAL"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("cs_gap_id", sa.Integer(), nullable=True),
        sa.Column("sales_case_id", sa.Integer(), nullable=True),
        sa.Column("vector_doc_id", sa.String(length=255), nullable=True),
        sa.Column(
            "sync_status",
            sa.Enum("NOT_SYNCED", "QUEUED", "INDEXING", "COMPLETED", "FAILED", "DELETING", name="sync_status"),
            nullable=False,
            server_default="NOT_SYNCED",
        ),
        sa.Column("sync_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("source_file", sa.String(length=512), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
    )
    op.create_index(op.f("ix_core_knowledge_items_id"), "core_knowledge_items", ["id"], unique=False)
    op.create_index(op.f("ix_core_knowledge_items_category"), "core_knowledge_items", ["category"], unique=False)
    op.create_table(
        "core_sync_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.Enum("CREATE", "UPDATE", "DELETE", name="sync_task_action"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("QUEUED", "RUNNING", "COMPLETED", "FAILED", name="sync_task_status"),
            nullable=False,
            server_default="QUEUED",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["core_knowledge_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
    )
    op.create_index(op.f("ix_core_sync_tasks_item_id"), "core_sync_tasks", ["item_id"], unique=False)
    op.create_index(op.f("ix_core_sync_tasks_status"), "core_sync_tasks", ["status"], unique=False)
    op.add_column("core_unanswered_questions", sa.Column("knowledge_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        None,
        "core_unanswered_questions",
        "core_knowledge_items",
        ["knowledge_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("core_sales_cases", sa.Column("knowledge_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        None,
        "core_sales_cases",
        "core_knowledge_items",
        ["knowledge_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
