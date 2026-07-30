import logging
import logging.handlers
import os
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

    # Anthropic (optional — LLM)
    anthropic_api_key: str = ""
    anthropic_llm_model: str = "claude-haiku-4-5-20251001"

    # Voyage AI (optional — embeddings when LLM_PROVIDER=anthropic)
    voyage_api_key: str = ""

    # Embeddings
    embed_dims: int = 768

    # Chunking
    chunk_size: int = 512
    chunk_overlap: int = 50

    # Retrieval
    top_k: int = 5

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/app.log"

    def configure_logging(self) -> None:
        level = getattr(logging, self.log_level.upper(), logging.INFO)
        fmt = "%(asctime)s %(levelname)s %(name)s — %(message)s"
        formatter = logging.Formatter(fmt)

        root = logging.getLogger()
        root.setLevel(level)

        # Console handler (captured by docker logs)
        if not root.handlers:
            console = logging.StreamHandler()
            console.setFormatter(formatter)
            root.addHandler(console)

        # Rotating file handler — 10 MB per file, keep 5 backups
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            self.log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


@lru_cache
def get_settings() -> Settings:
    return Settings()
