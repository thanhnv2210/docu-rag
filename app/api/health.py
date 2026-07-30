import logging

from fastapi import APIRouter, Request

from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    settings = get_settings()
    pool = request.app.state.pool

    vector_count: int = await pool.fetchval("SELECT COUNT(*) FROM documents")

    return {
        "status": "ok",
        "vector_count": vector_count,
        "provider": settings.llm_provider,
        "embed_model": (
            settings.ollama_embed_model
            if settings.llm_provider == "ollama"
            else "voyage-3"
        ),
        "llm_model": (
            settings.ollama_llm_model
            if settings.llm_provider == "ollama"
            else settings.anthropic_llm_model
        ),
    }
