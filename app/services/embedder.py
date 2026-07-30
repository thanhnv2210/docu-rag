import logging
from abc import ABC, abstractmethod

import voyageai
from ollama import AsyncClient as OllamaAsyncClient

from app.config import Settings

logger = logging.getLogger(__name__)

_BATCH_SIZE = 64  # max texts per embedding API call


class Embedder(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""

    @property
    @abstractmethod
    def dims(self) -> int:
        """Dimensionality of embedding vectors produced by this embedder."""


class OllamaEmbedder(Embedder):
    def __init__(self, base_url: str, model: str) -> None:
        self._client = OllamaAsyncClient(host=base_url)
        self._model = model

    @property
    def dims(self) -> int:
        return 768  # nomic-embed-text output dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            response = await self._client.embed(model=self._model, input=batch)
            all_embeddings.extend(response.embeddings)
        logger.debug("OllamaEmbedder: embedded %d texts", len(texts))
        return all_embeddings


class VoyageEmbedder(Embedder):
    """Embedder backed by Voyage AI (voyage-3, 1024 dims)."""

    _MODEL = "voyage-3"
    _DIMS = 1024

    def __init__(self, api_key: str) -> None:
        self._client = voyageai.AsyncClient(api_key=api_key)

    @property
    def dims(self) -> int:
        return self._DIMS

    async def embed(self, texts: list[str]) -> list[list[float]]:
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            result = await self._client.embed(batch, model=self._MODEL)
            all_embeddings.extend(result.embeddings)
        logger.debug("VoyageEmbedder: embedded %d texts", len(texts))
        return all_embeddings


def make_embedder(settings: Settings) -> Embedder:
    if settings.llm_provider == "anthropic":
        if not settings.voyage_api_key:
            raise ValueError("VOYAGE_API_KEY must be set when LLM_PROVIDER=anthropic")
        logger.info("Embedder: VoyageEmbedder (voyage-3)")
        return VoyageEmbedder(api_key=settings.voyage_api_key)

    logger.info(
        "Embedder: OllamaEmbedder (model=%s, url=%s)",
        settings.ollama_embed_model,
        settings.ollama_base_url,
    )
    return OllamaEmbedder(
        base_url=settings.ollama_base_url,
        model=settings.ollama_embed_model,
    )
