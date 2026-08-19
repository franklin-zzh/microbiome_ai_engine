"""add_asset_source_metadata

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-18 11:30:00.000000

为 core_knowledge_assets 扩展来源与抓取元数据：
- source_type: 来源类型（UPLOAD / CRAWLER / ETL）
- source_domain: 来源域名（如 fmtbio.com）
- source_url: 来源原始链接（如 https://fmtbio.com/hangye/...）
- extra_meta: 扩展元数据（JSON，存抓取深度、相对路径、批次 ID 等）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "core_knowledge_assets",
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="UPLOAD"),
    )
    op.add_column(
        "core_knowledge_assets",
        sa.Column("source_domain", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "core_knowledge_assets",
        sa.Column("source_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "core_knowledge_assets",
        sa.Column("extra_meta", sa.JSON(), nullable=True),
    )
    op.create_index(
        op.f("ix_core_knowledge_assets_source_type"),
        "core_knowledge_assets",
        ["source_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_core_knowledge_assets_source_domain"),
        "core_knowledge_assets",
        ["source_domain"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_core_knowledge_assets_source_domain"), table_name="core_knowledge_assets")
    op.drop_index(op.f("ix_core_knowledge_assets_source_type"), table_name="core_knowledge_assets")
    op.drop_column("core_knowledge_assets", "extra_meta")
    op.drop_column("core_knowledge_assets", "source_url")
    op.drop_column("core_knowledge_assets", "source_domain")
    op.drop_column("core_knowledge_assets", "source_type")
