# docu-rag — Technical Proposal

**Version:** 1.1
**Date:** 2026-07-30
**Status:** Implemented — v1 delivered and verified locally

---

## 1. Problem Statement

Markdown documentation is the default format for engineering knowledge — architecture decisions, API references, runbooks, and design rationale all live in `.md` files. But these documents are passive: developers must manually read and search them.

The problem has two layers:
1. **Discovery** — keyword search misses semantically related content ("What happens when a payment times out?" doesn't match a doc titled "Resilience Patterns")
2. **Synthesis** — even when the right doc is found, the answer may span multiple files and sections

**docu-rag** solves this with a RAG pipeline: ingest any markdown corpus, embed it into a vector database, and answer natural language questions by retrieving the most relevant chunks and generating a grounded, streaming response.

---

## 2. Goals

### Must have (MVP)
- Ingest a folder of markdown files: chunk, embed, store in pgvector
- Answer natural language questions with streaming SSE responses
- Hybrid search: vector similarity + full-text keyword merged with RRF
- Source attribution in every response (which files/chunks answered the question)
- Works fully offline using Ollama (no API key required)
- Docker Compose for one-command local setup
- Public demo corpus so anyone can clone and run

### Should have
- Anthropic API as an optional LLM/embedding provider
- `/health` endpoint with vector count
- Request logging with token count and latency
- GitHub Actions CI (lint + type-check + test)
- Deployed on Render with a live demo

### Out of scope (v1)
- Authentication / API keys
- Multi-user sessions or conversation history
- Document update / delete (full re-ingest only)
- Frontend UI (the API is the product; curl or any frontend can consume it)
- PDF or DOCX support

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Client                               │
│          (curl / React frontend / any HTTP client)          │
└───────────────────────┬─────────────────────────────────────┘
                        │  HTTP / SSE
┌───────────────────────▼─────────────────────────────────────┐
│                    FastAPI App                               │
│                                                             │
│  POST /ingest          POST /query           GET /health    │
│       │                     │                              │
│  ┌────▼────┐          ┌─────▼──────┐                       │
│  │Chunker  │          │ Retriever  │                       │
│  │(LangChn)│          │(Hybrid SQL)│                       │
│  └────┬────┘          └─────┬──────┘                       │
│       │                     │                              │
│  ┌────▼────┐          ┌─────▼──────┐                       │
│  │Embedder │          │ LLM Client │                       │
│  └────┬────┘          └─────┬──────┘                       │
└───────┼─────────────────────┼────────────────────────────┘
        │                     │  SSE stream
        │         ┌───────────▼──────────┐
        │         │   Ollama (default)   │
        │         │   or Anthropic API   │
        │         └──────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────┐
│              PostgreSQL 16 + pgvector                    │
│                                                          │
│  Table: documents                                        │
│  ├── id, file_path, title, chunk_index                   │
│  ├── content (TEXT)          ← full-text index (GIN)     │
│  ├── embedding (VECTOR(768)) ← IVFFlat index             │
│  └── metadata (JSONB)                                    │
└──────────────────────────────────────────────────────────┘
```

### Ingest flow
```
Markdown files
      │
      ▼
  Chunker (RecursiveCharacterTextSplitter, max 512 tokens, 50 overlap)
      │  splits on: \n## \n### \n\n  — preserves code blocks intact
      ▼
  Embedder.embed(texts: list[str]) → list[list[float]]
      │  Ollama: POST /api/embeddings  (nomic-embed-text, 768 dims)
      │  Anthropic: voyage-3 API       (1024 dims, set EMBED_DIMS=1024)
      ▼
  asyncpg bulk INSERT into documents table
      │
      ▼
  Response: { chunks_indexed, files_processed, duration_ms }
```

### Query flow
```
User question (string)
      │
      ▼
  Embedder.embed([question]) → query_vector
      │
      ├──────────────────────────────┐
      ▼                              ▼
  Vector search                 Full-text search
  (pgvector cosine distance)    (PostgreSQL ts_rank)
  top_k * 2 candidates          top_k * 2 candidates
      │                              │
      └──────────────┬───────────────┘
                     ▼
              RRF merge → top_k final chunks
                     │
                     ▼
         Build prompt: system + retrieved context + question
                     │
                     ▼
         LLMClient.stream_chat(messages) → AsyncGenerator[str]
                     │
                     ▼
         SSE: stream token events → final metadata event → [DONE]
```

---

## 4. API Specification

### POST /ingest

Indexes all markdown files found recursively under `corpus_path`.

**Request**
```json
{
  "corpus_path": "corpus/fintech-demo",
  "reset": false
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `corpus_path` | string | required | Path to markdown folder (relative to repo root or absolute) |
| `reset` | boolean | `false` | If true, deletes all existing chunks from this corpus before re-indexing |

**Response 200**
```json
{
  "chunks_indexed": 142,
  "files_processed": 7,
  "duration_ms": 1240
}
```

**Response 422** — validation error (invalid path)
**Response 500** — database or embedding error

---

### POST /query

Retrieves relevant chunks and streams a grounded answer via SSE.

**Request**
```json
{
  "question": "How does the transaction state machine handle payment failures?",
  "top_k": 5
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `question` | string | required | Natural language question |
| `top_k` | integer | `5` | Number of chunks to retrieve and include in context |

**Response** — `Content-Type: text/event-stream`

```
data: {"type": "token", "content": "When a payment"}

data: {"type": "token", "content": " fails, the transaction"}

data: {"type": "token", "content": " transitions to FAILED state..."}

data: {"type": "metadata", "sources": [
  {"file_path": "corpus/fintech-demo/transaction-state-machine.md", "chunk_index": 3, "title": "Failure Handling"},
  {"file_path": "corpus/fintech-demo/resilience-patterns.md", "chunk_index": 1, "title": "Retry Policy"}
], "latency_ms": 980, "tokens_used": 312, "provider": "ollama"}

data: [DONE]
```

---

### GET /health

**Response 200**
```json
{
  "status": "ok",
  "vector_count": 142,
  "provider": "ollama",
  "embed_model": "nomic-embed-text",
  "llm_model": "llama3.2"
}
```

---

## 5. Data Model

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path   TEXT        NOT NULL,
    title       TEXT,
    chunk_index INTEGER     NOT NULL,
    content     TEXT        NOT NULL,
    embedding   VECTOR(768),
    metadata    JSONB       DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_embedding
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_documents_fts
    ON documents USING gin(to_tsvector('english', content));
```

**Notes:**
- `EMBED_DIMS=768` for Ollama `nomic-embed-text`; set to `1024` if using Anthropic `voyage-3`
- IVFFlat index is **deferred**: it is not created on startup (IVFFlat requires data to train on). `schema.py` creates only the GIN FTS index at startup; the IVFFlat index is dropped and rebuilt by `ensure_vector_index()` called at the end of each `/ingest` request
- IVFFlat with `lists=100` is appropriate for corpora up to ~1M chunks; switch to HNSW for larger datasets
- `metadata` JSONB stores arbitrary front-matter extracted from markdown (e.g., `{"tags": ["resilience"], "version": "1.2"}`)

---

## 6. Hybrid Search — RRF Implementation

Reciprocal Rank Fusion merges two ranked lists into one without requiring score normalisation.

```sql
-- Vector search: returns top_k * 2 rows ranked by cosine distance
WITH vector_results AS (
    SELECT id, content, file_path, title, chunk_index,
           ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS vector_rank
    FROM documents
    ORDER BY embedding <=> $1::vector
    LIMIT $2
),
-- Full-text search: returns top_k * 2 rows ranked by ts_rank
fts_results AS (
    SELECT id, content, file_path, title, chunk_index,
           ROW_NUMBER() OVER (ORDER BY ts_rank(to_tsvector('english', content), query) DESC) AS fts_rank
    FROM documents, plainto_tsquery('english', $3) query
    WHERE to_tsvector('english', content) @@ query
    ORDER BY ts_rank(to_tsvector('english', content), query) DESC
    LIMIT $2
),
-- RRF merge: score = 1/(k + rank), k=60 is the standard constant
rrf AS (
    SELECT
        COALESCE(v.id, f.id) AS id,
        COALESCE(v.content, f.content) AS content,
        COALESCE(v.file_path, f.file_path) AS file_path,
        COALESCE(v.title, f.title) AS title,
        COALESCE(v.chunk_index, f.chunk_index) AS chunk_index,
        COALESCE(1.0 / (60 + v.vector_rank), 0) +
        COALESCE(1.0 / (60 + f.fts_rank), 0) AS rrf_score
    FROM vector_results v
    FULL OUTER JOIN fts_results f ON v.id = f.id
)
SELECT * FROM rrf ORDER BY rrf_score DESC LIMIT $4;
```

---

## 7. Service Abstractions

### Embedder (ABC)
```python
class Embedder(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def dims(self) -> int: ...

class OllamaEmbedder(Embedder): ...
class AnthropicEmbedder(Embedder): ...
```

### LLMClient (ABC)
```python
class LLMClient(ABC):
    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> AsyncGenerator[str, None]: ...

class OllamaLLMClient(LLMClient): ...
class AnthropicLLMClient(LLMClient): ...
```

Both are instantiated once at app startup via `lifespan` and stored in `app.state`.

---

## 8. Chunking Strategy

**Tool:** `langchain_text_splitters.RecursiveCharacterTextSplitter`

**Separators (in order of priority):**
1. `\n# ` — H1 heading
2. `\n## ` — H2 heading
3. `\n### ` — H3 heading
4. `\n\n` — paragraph break
5. `\n` — line break
6. ` ` — word boundary

**Settings:**
- `chunk_size = 512` (tokens, not characters — use `tiktoken` for accurate measurement)
- `chunk_overlap = 50`
- Code blocks (` ``` `) are never split mid-block — detected and treated as atomic units

**Metadata extracted per chunk:**
- `file_path` — relative path from corpus root
- `title` — nearest preceding heading (H1 or H2)
- `chunk_index` — sequential index within the file
- Any YAML front-matter key-value pairs

---

## 9. Streaming Implementation

`/query` returns a `StreamingResponse` with `media_type="text/event-stream"` wrapping an async generator. `sse-starlette` is installed but `StreamingResponse` is used directly for full control over framing and headers:

```python
async def token_stream(question: str, chunks: list[Chunk]) -> AsyncGenerator[str, None]:
    prompt = build_prompt(question, chunks)
    async for token in llm.stream_chat(messages=[{"role": "user", "content": prompt}]):
        yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
    yield f"data: {json.dumps({'type': 'metadata', 'sources': [...], 'latency_ms': ..., 'tokens_used': ...})}\n\n"
    yield "data: [DONE]\n\n"

return StreamingResponse(token_stream(...), media_type="text/event-stream",
                         headers={"X-Accel-Buffering": "no"})
```

`X-Accel-Buffering: no` disables nginx proxy buffering so tokens reach the client immediately (required on Render). The client (React or curl) reads `data:` lines and renders tokens as they arrive.

---

## 10. Observability

### Logging

Structured logs are written to both stdout (for `docker logs`) and a rotating file (`logs/app.log` on the host, mounted via `./logs:/app/logs`).

Two named loggers:
- **`app.*`** — application events (startup, ingest, DB pool lifecycle, errors)
- **`access`** — one line per HTTP request

Access log format:
```
2026-07-30 09:59:55,000 INFO access — GET /health 200 7ms client="192.168.0.1"
```

Implemented as a FastAPI HTTP middleware in `app/main.py` using `time.perf_counter()` for sub-millisecond precision. The middleware runs for all routes including SSE streams; the logged duration covers the full streaming response.

**Rotation:** 10 MB per file, 5 backups kept (`app.log`, `app.log.1` … `app.log.5`), configured via `logging.handlers.RotatingFileHandler`.

**Configuration:**

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Python log level |
| `LOG_FILE` | `logs/app.log` | Rotating log file path |

---

## 11. Demo Corpus — FinPay Platform

`corpus/fintech-demo/` ships with the repo. It documents a **fictional** payment platform called **FinPay**. No real company names, real endpoints, or proprietary information appear anywhere in these files.

| File | Content |
|---|---|
| `system-overview.md` | C4 container view in prose — 8 microservices, 3 payment hubs, event bus |
| `transaction-state-machine.md` | State lifecycle: PENDING → SUBMITTED → CONFIRMED → SETTLED / FAILED / CANCELLED |
| `payment-hub-integrations.md` | Fictional hubs: ArcaPay, SwiftHub, LocalPay — adapter pattern, error codes |
| `database-schema.md` | PostgreSQL schema — transactions, transaction_audit, fx_rates tables |
| `api-reference.md` | REST API endpoints for initiating, querying, and cancelling transactions |
| `resilience-patterns.md` | Retry policy, circuit breaker, idempotency, dead letter queue |
| `deployment-runbook.md` | Kubernetes deployment, environment variables, health checks, rollback steps |

These files are designed to produce realistic, multi-hop RAG questions — e.g., "What retry policy applies when ArcaPay returns a timeout?" requires joining `payment-hub-integrations.md` and `resilience-patterns.md`.

---

## 12. Technology Decisions & Rationale

| Decision | Choice | Why |
|---|---|---|
| Web framework | FastAPI | Native async, Pydantic v2 integration, auto OpenAPI docs, SSE support |
| Vector store | pgvector | Already know PostgreSQL deeply; avoids a second infrastructure dependency; production-proven |
| Default LLM/embed | Ollama | Zero cost, works offline, anyone can clone and run without an API key |
| Orchestration | LangChain (chunking only) | Avoids LangChain abstraction for retrieval — SQL is more transparent and controllable |
| Hybrid search | pgvector + `ts_rank` + RRF | Industry standard; demonstrates understanding beyond pure vector search |
| DB driver | asyncpg | Fastest async PostgreSQL driver; no ORM overhead needed |
| Validation | Pydantic v2 | FastAPI-native; v2 is 10–20x faster than v1 |
| Deployment | Render | Free tier supports Docker services + managed PostgreSQL; no credit card needed |
| CI | GitHub Actions | Free for public repos; ruff + mypy + pytest is a clean, fast pipeline |

---

## 13. Implementation Milestones

### M1 — Scaffold ✅
- [x] `CLAUDE.md`, `PROPOSAL.md`, `README.md`
- [x] `.env.example`, `.gitignore`, `requirements.txt`, `requirements-dev.txt`
- [x] Project directory structure (empty `__init__.py` files)
- [x] `app/config.py` — pydantic-settings Settings class

### M2 — Database layer ✅
- [x] `docker-compose.yml` — pgvector/pgvector:pg16 + app service
- [x] `app/db/connection.py` — asyncpg pool, lifespan hook
- [x] `app/db/schema.py` — DDL execution on startup
- [x] `app/main.py` — FastAPI app factory with lifespan

### M3 — Ingest pipeline ✅
- [x] `corpus/fintech-demo/` — 7 demo markdown files
- [x] `app/services/chunker.py` — markdown-aware splitter
- [x] `app/services/embedder.py` — OllamaEmbedder + VoyageEmbedder (via `voyageai` SDK, not Anthropic SDK)
- [x] `app/models/schemas.py` — IngestRequest, IngestResponse
- [x] `app/api/ingest.py` — POST /ingest endpoint

### M4 — Query & streaming ✅
- [x] `app/services/retriever.py` — hybrid search SQL + RRF
- [x] `app/services/llm.py` — OllamaLLMClient + AnthropicLLMClient
- [x] `app/models/schemas.py` — QueryRequest, SSE event models
- [x] `app/api/query.py` — POST /query SSE endpoint
- [x] `GET /health` endpoint

### M5 — Tests & CI ✅
- [x] `tests/conftest.py` — pytest fixtures, MockEmbedder + MockLLMClient, monkeypatched before lifespan
- [x] `tests/test_chunker.py` — 12 unit tests for chunking logic
- [x] `tests/test_retriever.py` — 11 unit tests for RRF merge and SQL helpers
- [x] `tests/test_api.py` — 14 integration tests for /ingest and /query (skip markers if no DB)
- [x] `.github/workflows/ci.yml` — ruff + mypy + pytest with pgvector service

### M6 — Docker & deploy ✅
- [x] `Dockerfile` — multi-stage build, non-root user, corpus bundled in image
- [x] `render.yaml` — free-tier Docker web service + managed PostgreSQL
- [x] `README.md` — complete with architecture diagram, API reference, deploy instructions
- [x] `public/favicon.svg` — teal stacked-pages with spark icon

### Post-M6 — Observability ✅
- [x] `app/config.py` — rotating file handler (`RotatingFileHandler`, 10 MB / 5 backups) added to `configure_logging()`; `LOG_FILE` env var added
- [x] `app/main.py` — HTTP access logging middleware logs method, path, status, duration, client IP to `access` logger
- [x] `docker-compose.yml` — `./logs:/app/logs` bind mount; `LOG_FILE` env var set
- [x] `requirements.txt` — `httpx` pinned to `>=0.27.0,<0.28.0` (upper bound required by `ollama==0.4.4`)

---

## 14. Non-Functional Requirements

| NFR | Target |
|---|---|
| Query latency (Ollama, local) | < 3s to first token |
| Query latency (Anthropic API) | < 1s to first token |
| Ingest throughput | > 50 chunks/sec |
| Max corpus size (v1) | ~10,000 chunks (~50 MB markdown) |
| Test coverage | > 70% on services layer |
| CI pipeline duration | < 3 minutes |
