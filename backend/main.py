from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import admin, cs, knowledge
from app.core.database import engine
from app.models import knowledge as models


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=engine)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="gut-health-agent-platform",
        description="肠道健康 Agent 平台：RAG + 客服 + 销售 Copilot",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(cs.router, prefix="/api/v1")
    app.include_router(knowledge.router, prefix="/api/v1")
    return app


app = create_app()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
