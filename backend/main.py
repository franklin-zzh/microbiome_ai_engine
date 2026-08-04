from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent_cs import models as cs_models
from app.agent_cs.router import chat_router, wechat_router
from app.agent_sales import models as sales_models  # noqa: F401  注册 LeadsPreview 到 BaseCs
from app.agent_sales.router import router as sales_router
from app.core.database import core_engine, cs_engine
from app.core.redis import redis_health
from app.knowledge import models as knowledge_models
from app.knowledge.router import admin_router, cs_router, router as knowledge_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MySQL 双库建表：mb_ai_core（知识库体系） + mb_ai_cs（客服会话体系 + 销售线索）
    knowledge_models.Base.metadata.create_all(bind=core_engine)
    cs_models.BaseCs.metadata.create_all(bind=cs_engine)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="agent_cs",
        description="AI 客服 Agent（agent_cs）：微信接入 + Dify RAG（engine_rag）+ 对话日志湖 + 会话状态机",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(cs_router, prefix="/api/v1")
    app.include_router(knowledge_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(sales_router, prefix="/api/v1")
    app.include_router(wechat_router, prefix="/api/v1")
    app.include_router(wechat_router, prefix="")  # 兼容 https://wx.fmtcloud.cn/wx/msg
    return app


app = create_app()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", **redis_health()}
