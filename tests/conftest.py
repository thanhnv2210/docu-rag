"""
Shared fixtures and helpers for the docu-rag test suite.

Unit tests (test_chunker, test_retriever) require no external services.
Integration tests (test_api) require DATABASE_URL to be set and will be
skipped automatically when it is not.
"""

import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.services.embedder import Embedder
from app.services.llm import LLMClient

# ---------------------------------------------------------------------------
# Skip marker — applied to tests that need a live PostgreSQL instance
# ---------------------------------------------------------------------------

skip_without_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping integration tests",
)


# ---------------------------------------------------------------------------
# Mock providers
# ---------------------------------------------------------------------------

class MockEmbedder(Embedder):
    """Returns a deterministic 768-dim vector for any input."""

    @property
    def dims(self) -> int:
        return 768

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.01] * 768 for _ in texts]


class MockLLMClient(LLMClient):
    """Yields a two-token response without calling any external service."""

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> AsyncGenerator[str, None]:
        yield "Test "
        yield "response."


# ---------------------------------------------------------------------------
# App client fixture (integration — needs real DB, mocked embedder + LLM)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    """
    httpx AsyncClient wired to the FastAPI app.

    The DB lifespan runs for real (requires DATABASE_URL).
    Embedder and LLM are replaced with in-process mocks so no Ollama/Anthropic
    API is needed in CI.
    """
    from app.services import embedder as embedder_mod
    from app.services import llm as llm_mod

    monkeypatch.setattr(embedder_mod, "make_embedder", lambda _s: MockEmbedder())
    monkeypatch.setattr(llm_mod, "make_llm_client", lambda _s: MockLLMClient())

    # Import app AFTER patching so lifespan picks up the mocked factories
    from app.main import create_app

    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
