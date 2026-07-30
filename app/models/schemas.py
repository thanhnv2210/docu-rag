from pydantic import BaseModel, Field


# ── Ingest ────────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    corpus_path: str = Field(..., description="Path to folder of markdown files (relative or absolute)")
    reset: bool = Field(False, description="Delete existing chunks for this corpus before re-indexing")


class IngestResponse(BaseModel):
    chunks_indexed: int
    files_processed: int
    duration_ms: int


# ── Query (added in M4) ───────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Natural language question")
    top_k: int = Field(5, ge=1, le=20, description="Number of chunks to retrieve")


class ChunkSource(BaseModel):
    file_path: str
    chunk_index: int
    title: str | None = None


class TokenEvent(BaseModel):
    type: str = "token"
    content: str


class MetadataEvent(BaseModel):
    type: str = "metadata"
    sources: list[ChunkSource]
    latency_ms: int
    tokens_used: int
    provider: str
