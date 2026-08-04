from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "agent_cs"

    debug: bool = False

    # ============ MySQL 双库（沿用本地 Docker 部署的 global-mysql8，端口 3306）============
    # mb_ai_core：公共用户 / 知识库索引（knowledge_items / unanswered_questions / sales_cases）
    core_database_url: str = "mysql+pymysql://root:fumate@localhost:3306/mb_ai_core?charset=utf8mb4"
    # mb_ai_cs：客服 Agent 专有库（cs_chat_logs / session_state / leads_preview）
    cs_database_url: str = "mysql+pymysql://root:fumate@localhost:3306/mb_ai_cs?charset=utf8mb4"

    # ============ Redis（本地 Docker 部署，端口 6380，密码 fumate）============
    # 本项目独占 Redis db3（已核查 db0-15 全部为空，无历史项目占用；db0-2 预留给其他项目）
    redis_url: str = "redis://:fumate@localhost:6380/3"
    session_ttl_seconds: int = 1800          # 会话状态 TTL（默认 30 分钟）
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
