from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent_cs import models as cs_models  # noqa: F401  注册 CsChatLog / SessionState 到 Base
from app.agent_cs.router import chat_router, wechat_router
from app.agent_sales import models as sales_models  # noqa: F401  注册 LeadsPreview 到 Base
from app.agent_sales.router import router as sales_router
from app.core.auth_router import router as auth_router
from app.core.config import get_settings
from app.core.redis import redis_health
from app.knowledge import models as knowledge_models  # noqa: F401  注册 core_* 表（CS 文档五表等）到 Base
from app.knowledge.router import admin_router, cs_router, router as knowledge_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 表结构统一由 Alembic 迁移管理（alembic upgrade head），不再使用 create_all
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="agent_cs",
        description="AI 客服 Agent（agent_cs）：微信接入 + Dify RAG（engine_rag）+ 对话日志湖 + 会话状态机",
        version="0.2.0",
        lifespan=lifespan,
    )
    # P0：CORS 白名单从 .env 注入（逗号分隔），禁止 *（含个人信息的接口不允许任意来源跨域）
    cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        # 注意：allow_origins 含通配时不能开启 allow_credentials（浏览器拒绝该组合）
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(cs_router, prefix="/api/v1")
    app.include_router(knowledge_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(sales_router, prefix="/api/v1")
    app.include_router(wechat_router, prefix="/api/v1")
    app.include_router(wechat_router, prefix="")  # 兼容 https://wx.fmtcloud.cn/wx/msg
    # 审核后台单页（backend/public/admin）；uploads 不挂静态目录，只走鉴权下载接口
    app.mount(
        "/admin",
        StaticFiles(directory=Path(__file__).resolve().parent / "public" / "admin", html=True),
        name="admin",
    )
    return app


app = create_app()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", **redis_health()}
