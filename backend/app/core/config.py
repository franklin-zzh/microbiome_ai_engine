from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 统一读取项目根目录 .env（无论从哪个目录启动 backend 都生效）
ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

# 生产必填项（P0 安全基线：默认凭据拒绝，缺失则启动失败，杜绝漏配裸奔）
REQUIRED_SETTINGS = (
    "database_url",
    "redis_url",
    "jwt_secret",
    "admin_username",
    "admin_password",
    "internal_api_key",
)


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
    # 注意：不再提供默认凭据（P0），必须从 .env 注入，空值启动即失败。
    database_url: str = ""
    # pytest 使用的测试库（alembic upgrade head 建表）
    test_database_url: str = ""

    # ============ Redis（本地 Docker 部署，端口 6380）============
    # 本项目独占 Redis db3（已核查 db0-15 全部为空，无历史项目占用；db0-2 预留给其他项目）
    redis_url: str = ""
    session_ttl_seconds: int = 1800          # 会话状态 TTL（默认 30 分钟）
    session_retention_days: int = 30         # 会话不活跃过期后，状态行再保留天数（之后惰性清理/脚本删除）
    negative_streak_threshold: int = 2       # 连续负面情绪 >= 2 转 HUMAN_MODE

    # ============ Dify（共享知识库引擎 engine_rag，未来可拆分为独立服务）============
    dify_base_url: str = "http://192.168.110.16:3080/v1"
    dify_api_key: str = ""
    # 域 -> 知识库映射（留空 = 未启用，启用前 dataset_id_for_domain 会拒绝该域/形态）
    cs_dataset_id: str = ""          # CS 问答库（qa_model）
    cs_doc_dataset_id: str = ""      # CS 长文档库（text_model/hierarchical_model，双库预留）
    sales_dataset_id: str = ""       # 销售案例库
    doctor_dataset_id: str = ""      # 医生域独立库（Phase 3 预留）

    cs_score_threshold: float = 0.65

    # 微信客服配置
    wxkf_corp_id: str = ""
    wxkf_secret: str = ""
    wxkf_token: str = ""
    wxkf_encoding_aes_key: str = ""

    # ============ 安全基线（P0，全部必填）============
    # JWT 签发密钥：生成方式 python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret: str = ""
    jwt_expire_minutes: int = 720            # token 有效期（默认 12 小时）
    # 管理后台单账号（角色模型演进见 docs/ARCHITECTURE-REVIEW.md R5）
    admin_username: str = ""
    admin_password: str = ""
    # Dify Workflow 回调 / 内部异步链路的共享密钥（Header: X-API-Key）
    internal_api_key: str = ""
    # CORS 白名单（逗号分隔；生产环境填真实前端域名，禁止 *）
    cors_origins: str = ""

    @model_validator(mode="after")
    def _require_production_secrets(self):
        missing = [name.upper() for name in REQUIRED_SETTINGS if not getattr(self, name)]
        if missing:
            raise ValueError(
                "缺少必填配置项（请复制 .env.example 为项目根目录 .env 并填入真实值）: "
                + ", ".join(missing)
            )
        if len(self.jwt_secret) < 32:
            raise ValueError(
                "JWT_SECRET 长度不足 32 字符（生成: python -c \"import secrets; print(secrets.token_urlsafe(48))\"）"
            )
        if "*" in [o.strip() for o in self.cors_origins.split(",") if o.strip()]:
            raise ValueError("CORS_ORIGINS 禁止使用通配符 *（含个人信息的接口不允许任意来源跨域）")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
