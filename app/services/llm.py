import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator

import anthropic
from ollama import AsyncClient as OllamaAsyncClient

from app.config import Settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a precise technical assistant. Answer questions using ONLY the documentation context provided below.

Rules:
- Base your answer strictly on the provided context. Do not invent information.
- If the answer is not in the context, say "I cannot find this in the provided documentation."
- Be concise and direct. Use bullet points or code blocks where appropriate.
- When referencing specific details, mention which document they come from."""


class LLMClient(ABC):
    @abstractmethod
    def stream_chat(
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield text tokens from the LLM as they are generated."""


class OllamaLLMClient(LLMClient):
    def __init__(self, base_url: str, model: str) -> None:
        self._client = OllamaAsyncClient(host=base_url)
        self._model = model

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> AsyncGenerator[str, None]:
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        async for chunk in await self._client.chat(
            model=self._model,
            messages=full_messages,
            stream=True,
        ):
            token = chunk.message.content
            if token:
                yield token


class AnthropicLLMClient(LLMClient):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> AsyncGenerator[str, None]:
        kwargs: dict = dict(
            model=self._model,
            messages=messages,
            max_tokens=2048,
        )
        if system:
            kwargs["system"] = system

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text


def make_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY must be set when LLM_PROVIDER=anthropic")
        logger.info("LLM: AnthropicLLMClient (model=%s)", settings.anthropic_llm_model)
        return AnthropicLLMClient(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_llm_model,
        )

    logger.info(
        "LLM: OllamaLLMClient (model=%s, url=%s)",
        settings.ollama_llm_model,
        settings.ollama_base_url,
    )
    return OllamaLLMClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_llm_model,
    )
