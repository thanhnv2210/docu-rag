import logging
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str

    # LLM provider selection
    llm_provider: Literal["ollama", "anthropic"] = "ollama"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "llama3.2"
    ollama_embed_model: str = "nomic-embed-text"

    # Anthropic (optional)
    anthropic_api_key: str = ""
    anthropic_llm_model: str = "claude-haiku-4-5-20251001"

    # Embeddings
    embed_dims: int = 768

    # Chunking
    chunk_size: int = 512
    chunk_overlap: int = 50

    # Retrieval
    top_k: int = 5

    # Logging
    log_level: str = "INFO"

    def configure_logging(self) -> None:
        logging.basicConfig(
            level=getattr(logging, self.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
