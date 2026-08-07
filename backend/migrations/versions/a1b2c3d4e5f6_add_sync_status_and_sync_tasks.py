"""add_sync_status_and_sync_tasks

Revision ID: a1b2c3d4e5f6
Revises: 6073b7f19ee8
Create Date: 2026-08-08 10:00:00.000000

知识项同步状态升级：
- core_knowledge_items 增加 sync_status / sync_attempts / last_sync_error / source_file
- 新建 core_sync_tasks 对账表（审核/驳回动作 -> 异步落 Dify 的 outbox）

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '6073b7f19ee8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 原生 SQL：保持 AFTER 顺序（op.add_column 不支持 existing_* 参数）
    op.execute(
        "ALTER TABLE core_knowledge_items "
        "ADD COLUMN sync_status ENUM('NOT_SYNCED','QUEUED','INDEXING','COMPLETED','FAILED','DELETING') "
        "NOT NULL DEFAULT 'NOT_SYNCED' AFTER vector_doc_id, "
        "ADD COLUMN sync_attempts INT NOT NULL DEFAULT 0 AFTER sync_status, "
        "ADD COLUMN last_sync_error TEXT NULL AFTER sync_attempts, "
        "ADD COLUMN source_file VARCHAR(512) NULL AFTER last_sync_error"
    )
    # KnowledgeStatus 新增 REVOKED（下线状态）：扩展既有 knowledge_status ENUM 列
    op.execute(
        "ALTER TABLE core_knowledge_items "
        "MODIFY status ENUM('DRAFT','PENDING','APPROVED','REJECTED','REVOKED') NOT NULL"
    )

    op.create_table(
        'core_sync_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column(
            'action',
            sa.Enum('CREATE', 'UPDATE', 'DELETE', name='sync_task_action'),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', name='sync_task_status'),
            nullable=False,
            server_default='QUEUED',
        ),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('next_retry_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['item_id'], ['core_knowledge_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        mysql_charset='utf8mb4',
    )
    op.create_index(op.f('ix_core_sync_tasks_item_id'), 'core_sync_tasks', ['item_id'], unique=False)
    op.create_index(op.f('ix_core_sync_tasks_status'), 'core_sync_tasks', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_core_sync_tasks_status'), table_name='core_sync_tasks')
    op.drop_index(op.f('ix_core_sync_tasks_item_id'), table_name='core_sync_tasks')
    op.drop_table('core_sync_tasks')
    op.execute(
        "ALTER TABLE core_knowledge_items "
        "DROP COLUMN source_file, "
        "DROP COLUMN last_sync_error, "
        "DROP COLUMN sync_attempts, "
        "DROP COLUMN sync_status"
    )
