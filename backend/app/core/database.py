from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import get_settings

settings = get_settings()

# ============ MySQL 双库 ============
# mb_ai_core：公共用户 / 知识库索引（knowledge_items / unanswered_questions / sales_cases）
core_engine = create_engine(settings.core_database_url, echo=settings.debug, future=True)
CoreSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=core_engine, future=True)

# mb_ai_cs：客服 Agent 专有库（cs_chat_logs / session_state / leads_preview）
cs_engine = create_engine(settings.cs_database_url, echo=settings.debug, future=True)
CsSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cs_engine, future=True)

# 基类：core 库（知识库体系）与 cs 库（客服会话体系）各自独立的 declarative Base
Base = declarative_base()      # -> mb_ai_core
BaseCs = declarative_base()    # -> mb_ai_cs


def get_db():
    """mb_ai_core 依赖注入（知识库 / 未解答问题 / 销售案例）"""
    db = CoreSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_cs():
    """mb_ai_cs 依赖注入（对话日志 / 会话状态 / 线索）"""
    db = CsSessionLocal()
    try:
        yield db
    finally:
        db.close()
