import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import get_settings

settings = get_settings()

# ============ MySQL 单库（mb_ai_engine）============
# 统一 schema：core_*（知识库体系）+ cs_*（客服会话体系 + 销售线索），
# 通过 __tablename__ 前缀隔离领域；表结构变更统一走 Alembic 迁移。
engine = create_engine(settings.database_url, echo=settings.sqlalchemy_echo, future=True)
# root logger 被 app/core/logging.py 设为 INFO；不显式压低 sqlalchemy.engine，
# 即使 echo=False 也会继承 root 级别把每条 SQL 打到日志。这里压到 WARNING，
# 仅保留连接错误等关键信息；排障时 .env 设 SQLALCHEMY_ECHO=true 恢复 SQL 输出。
if not settings.sqlalchemy_echo:
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

# 统一声明基类：全部业务表（core_* / cs_*）注册到同一 metadata，供 Alembic autogenerate
Base = declarative_base()


def get_db():
    """mb_ai_engine 单库依赖注入（core_* 知识库体系 + cs_* 客服会话体系）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
