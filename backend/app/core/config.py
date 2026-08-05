from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# 统一读取项目根目录 .env（无论从哪个目录启动 backend 都生效）
ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "agent_cs"

    debug: bool = False

    # ============ MySQL 单库（沿用本地 Docker 部署的 global-mysql8，端口 3306）============
    # mb_ai_engine：统一 schema —— core_*（知识库体系）+ cs_*（客服会话体系 + 销售线索），
    # 领域隔离靠表名前缀；表结构变更统一走 Alembic 迁移（不再使用 create_all）。
    database_url: str = "mysql+pymysql://root:fumate@localhost:3306/mb_ai_engine?charset=utf8mb4"
    # pytest 使用的测试库（alembic upgrade head 建表）
    test_database_url: str = "mysql+pymysql://root:fumate@localhost:3306/mb_ai_engine_test?charset=utf8mb4"

    # ============ Redis（本地 Docker 部署，端口 6380，密码 fumate）============
    # 本项目独占 Redis db3（已核查 db0-15 全部为空，无历史项目占用；db0-2 预留给其他项目）
    redis_url: str = "redis://:fumate@localhost:6380/3"
    session_ttl_seconds: int = 1800          # 会话状态 TTL（默认 30 分钟）
    session_retention_days: int = 30         # 会话不活跃过期后，状态行再保留天数（之后惰性清理/脚本删除）
    negative_streak_threshold: int = 2       # 连续负面情绪 >= 2 转 HUMAN_MODE

    # ============ Dify（共享知识库引擎 engine_rag，未来可拆分为独立服务）============
    dify_base_url: str = "https://api.dify.ai/v1"
    dify_api_key: str = ""
    cs_dataset_id: str = ""
    sales_dataset_id: str = ""

    cs_score_threshold: float = 0.65

    # 微信客服配置
    wxkf_corp_id: str = ""
    wxkf_secret: str = ""
    wxkf_token: str = ""
    wxkf_encoding_aes_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
