"""pytest 共享基建：单测试库 + Alembic 迁移建表

1. collection 前把 DATABASE_URL 指向 mb_ai_engine_test，
   保证 Settings / app engine 首次实例化即落在测试库（杜绝误写生产库）；
2. session 级 fixture：测试前清库（含 alembic_version）-> alembic upgrade head 建最新表结构
   （连索引/初始化数据一起刷入，替代旧的 create_all），测试结束后清空。
"""
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# P0 鉴权：测试用安全配置（环境变量优先于 .env，保证无 .env 的 CI 也可运行；
# 已有真实环境变量时不覆盖）。必须在 import get_settings 之前设置。
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789")
os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "testpass")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")

import pytest
from alembic import command
from alembic.config import Config

from app.core.config import get_settings

settings = get_settings()
os.environ["DATABASE_URL"] = settings.test_database_url


@pytest.fixture(scope="session", autouse=True)
def migrated_test_db():
    """测试会话开始前重建测试库表结构（alembic upgrade head），结束后清空。"""
    from sqlalchemy import create_engine

    from app.agent_cs import models  # noqa: F401  注册 cs_* 表到 Base.metadata
    from app.agent_sales import models  # noqa: F401  注册 cs_leads_preview
    from app.core.database import Base
    from app.knowledge import models  # noqa: F401  注册 core_* 表

    engine = create_engine(settings.test_database_url)
    # 旧知识项三表已随旧链路下线（core_knowledge_items/core_sync_tasks 已删表）；
    # 仍关闭外键检查后显式 DROP，保证清库顺序无关（DROP 顺序与建表顺序相反更稳）。
    with engine.begin() as conn:
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        # alembic_version 必须一并删除，否则 upgrade head 会误判已在最新版而跳过建表
        for table in (
            "alembic_version",
            "cs_chat_logs",
            "cs_leads_preview",
            "cs_session_state",
            "core_unanswered_questions",
            "core_sales_cases",
            "core_dify_document_blocks",
            "core_dify_publish_targets",
            "core_knowledge_document_versions",
            "core_knowledge_documents",
            "core_knowledge_assets",
        ):
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS `{table}`")
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")

    yield

    with engine.begin() as conn:
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        for table in (
            "alembic_version",
            "cs_chat_logs",
            "cs_leads_preview",
            "cs_session_state",
            "core_unanswered_questions",
            "core_sales_cases",
            "core_dify_document_blocks",
            "core_dify_publish_targets",
            "core_knowledge_document_versions",
            "core_knowledge_documents",
            "core_knowledge_assets",
        ):
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS `{table}`")
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
