"""add_chat_type_and_chat_id_to_cs_chat_logs

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-19 16:15:00.000000

为 cs_chat_logs 对话日志表增加一级检索索引字段：
- chat_type: 会话类型（single 1v1私聊 / group 群聊 / wxkf 微信客服）
- chat_id: 群聊 ID（用于按群维度高频检索与复盘）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "cs_chat_logs",
        sa.Column("chat_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "cs_chat_logs",
        sa.Column("chat_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        op.f("ix_cs_chat_logs_chat_type"),
        "cs_chat_logs",
        ["chat_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cs_chat_logs_chat_id"),
        "cs_chat_logs",
        ["chat_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_cs_chat_logs_chat_id"), table_name="cs_chat_logs")
    op.drop_index(op.f("ix_cs_chat_logs_chat_type"), table_name="cs_chat_logs")
    op.drop_column("cs_chat_logs", "chat_id")
    op.drop_column("cs_chat_logs", "chat_type")
