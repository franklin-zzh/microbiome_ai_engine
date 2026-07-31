from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "gut-health-agent-platform"
    debug: bool = False

    database_url: str = "postgresql://postgres:fumate@localhost:5433/gut_health"

    dify_base_url: str = "https://api.dify.ai/v1"
    dify_api_key: str = ""
    cs_dataset_id: str = ""
    sales_dataset_id: str = ""

    cs_score_threshold: float = 0.65


@lru_cache
def get_settings() -> Settings:
    return Settings()
