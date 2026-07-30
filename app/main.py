import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import get_settings
from app.db.connection import close_pool, create_pool
from app.db.schema import init_schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    settings.configure_logging()

    # Database
    pool = await create_pool(settings.database_url)
    async with pool.acquire() as conn:
        await init_schema(conn, settings.embed_dims)
    app.state.pool = pool

    # Providers — imported here to avoid circular imports at module level
    from app.services.embedder import make_embedder
    from app.services.llm import make_llm_client

    app.state.embedder = make_embedder(settings)
    app.state.llm = make_llm_client(settings)
    app.state.settings = settings

    logger.info(
        "docu-rag started | provider=%s | embed_dims=%d",
        settings.llm_provider,
        settings.embed_dims,
    )

    yield

    await close_pool(pool)
    logger.info("docu-rag shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="docu-rag",
        description="RAG API — ingest markdown docs, query with streaming SSE responses.",
        version="1.0.0",
        lifespan=lifespan,
    )

    from app.api.health import router as health_router
    from app.api.ingest import router as ingest_router
    from app.api.query import router as query_router

    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(query_router)

    return app


app = create_app()
