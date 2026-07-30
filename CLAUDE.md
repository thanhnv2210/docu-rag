# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Project Overview

**docu-rag** — a production-grade RAG (Retrieval-Augmented Generation) API built with Python and FastAPI. It ingests any folder of markdown documents into a pgvector-backed PostgreSQL database and answers natural language questions via a streaming SSE endpoint.

This is a **public portfolio project** built to close skill gaps for AI engineer roles. All code must be clean, professional, and demonstrable. There is no internal or confidential data anywhere in this repo.

Read `PROPOSAL.md` for the full technical specification before starting any implementation work.

## Tech Stack

- **Runtime:** Python 3.12
- **Web framework:** FastAPI (async)
- **LLM orchestration:** LangChain
- **Vector store:** pgvector (PostgreSQL extension)
- **Database:** PostgreSQL 16
- **LLM (default):** Ollama — `llama3.2` model, runs locally, no API key needed
- **Embeddings (default):** Ollama — `nomic-embed-text` (768 dims)
- **LLM (optional override):** Anthropic API — `claude-haiku-4-5-20251001`
- **Embeddings (optional override):** Anthropic — `voyage-3`
- **Validation:** Pydantic v2
- **Testing:** pytest + pytest-asyncio
- **Containerisation:** Docker + Docker Compose
- **CI:** GitHub Actions
- **Deploy target:** Render (free tier)

## Project Structure

```
docu-rag/
├── app/
│   ├── main.py                  # FastAPI app factory, lifespan, router registration
│   ├── config.py                # Settings via pydantic-settings (reads .env)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── ingest.py            # POST /ingest
│   │   └── query.py             # POST /query  (SSE streaming)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── chunker.py           # Markdown-aware chunking
│   │   ├── embedder.py          # Embedder abstraction (Ollama / Anthropic)
│   │   ├── retriever.py         # Hybrid search: vector + keyword → RRF merge
│   │   └── llm.py               # LLM abstraction (Ollama / Anthropic streaming)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── connection.py        # asyncpg connection pool, lifespan management
│   │   └── schema.py            # DDL helpers: create tables, indexes
│   └── models/
│       ├── __init__.py
│       └── schemas.py           # All Pydantic v2 request/response models
├── corpus/
│   └── fintech-demo/            # Demo markdown docs — fictional FinPay platform
│       ├── system-overview.md
│       ├── transaction-state-machine.md
│       ├── payment-hub-integrations.md
│       ├── database-schema.md
│       ├── api-reference.md
│       ├── resilience-patterns.md
│       └── deployment-runbook.md
├── tests/
│   ├── conftest.py
│   ├── test_chunker.py
│   ├── test_retriever.py
│   └── test_api.py
├── .github/
│   └── workflows/
│       └── ci.yml               # lint (ruff) + type-check (mypy) + test (pytest)
├── docker-compose.yml           # API + PostgreSQL/pgvector
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── .gitignore
├── CLAUDE.md
├── PROPOSAL.md
└── README.md
```

## Key Architectural Decisions

**Pluggable LLM and embedder** — `LLMClient` and `Embedder` are abstract base classes. Ollama and Anthropic are concrete implementations selected via `LLM_PROVIDER` env var. Adding a new provider requires implementing two methods: `embed(texts)` and `stream_chat(messages)`.

**Hybrid search** — vector similarity alone misses exact keyword matches. The retriever runs two searches in parallel (pgvector cosine distance + PostgreSQL full-text `ts_rank`) then merges with Reciprocal Rank Fusion (RRF). This is the industry standard approach and a key interview talking point.

**Streaming first** — `/query` always streams via SSE. No buffered JSON response option. Each SSE event is typed: `{"type": "token", "content": "..."}` during generation, followed by a single `{"type": "metadata", ...}` event with sources, latency, and token count.

**Corpus is pluggable** — the `/ingest` endpoint accepts any `corpus_path`. The demo corpus (`corpus/fintech-demo/`) ships with the repo. Users can point it at their own docs folder without code changes.

**No LangChain for retrieval** — LangChain is used only for chunking (`RecursiveCharacterTextSplitter`) and the embedding abstraction. The retrieval and ranking logic is written directly in SQL + Python for full transparency and control. This is intentional — it shows understanding of what LangChain does under the hood.

## API Contract

### POST /ingest
```json
// Request
{ "corpus_path": "corpus/fintech-demo", "reset": false }

// Response
{ "chunks_indexed": 142, "duration_ms": 1240, "files_processed": 7 }
```

### POST /query  (SSE)
```json
// Request
{ "question": "How does the transaction state machine handle payment failures?", "top_k": 5 }

// SSE events
data: {"type": "token", "content": "When a payment "}
data: {"type": "token", "content": "fails, the transaction "}
...
data: {"type": "metadata", "sources": [{"file": "transaction-state-machine.md", "chunk": 3}], "latency_ms": 980, "tokens_used": 312}
data: [DONE]
```

### GET /health
```json
{ "status": "ok", "vector_count": 142, "provider": "ollama" }
```

## Database Schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path   TEXT NOT NULL,
    title       TEXT,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    embedding   VECTOR(768),        -- nomic-embed-text dims; 1536 if using voyage-3
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Vector index (IVFFlat for datasets < 1M rows)
CREATE INDEX IF NOT EXISTS idx_documents_embedding
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Full-text index
CREATE INDEX IF NOT EXISTS idx_documents_fts
    ON documents USING gin(to_tsvector('english', content));
```

## Environment Variables

All variables are in `.env` (gitignored). See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | (required) | asyncpg connection string |
| `LLM_PROVIDER` | `ollama` | `ollama` or `anthropic` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL. Set to `http://host.docker.internal:11434` when running inside Docker on macOS/Windows |
| `OLLAMA_LLM_MODEL` | `llama3.2` | Ollama chat model |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Ollama embedding model |
| `ANTHROPIC_API_KEY` | _(optional)_ | Required only if `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_LLM_MODEL` | `claude-haiku-4-5-20251001` | Anthropic chat model |
| `EMBED_DIMS` | `768` | Must match embedding model output dims |
| `CHUNK_SIZE` | `512` | Max tokens per chunk |
| `CHUNK_OVERLAP` | `50` | Overlap tokens between chunks |
| `TOP_K` | `5` | Default number of chunks to retrieve |
| `LOG_LEVEL` | `INFO` | Python log level |
| `LOG_FILE` | `logs/app.log` | Rotating log file path (10 MB/file, 5 backups). Mounted to host via `./logs:/app/logs` in docker-compose |

## Development Setup

### Prerequisites
- Docker + Docker Compose
- Python 3.12
- [Ollama](https://ollama.ai) running locally with `llama3.2` and `nomic-embed-text` pulled

### Local run (Docker)
```bash
cp .env.example .env
# edit .env if needed
docker compose up --build
# API available at http://localhost:8001
```

### Local run (without Docker)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
# Start PostgreSQL with pgvector separately, update DATABASE_URL in .env
uvicorn app.main:app --reload --port 8001
```

### Ingest the demo corpus
```bash
curl -X POST http://localhost:8001/ingest \
  -H "Content-Type: application/json" \
  -d '{"corpus_path": "corpus/fintech-demo", "reset": true}'
```

### Run tests
```bash
pytest tests/ -v
```

## Coding Standards

- **Typed everywhere** — all function signatures have type annotations; Pydantic v2 for all I/O models
- **Async throughout** — use `async def` for all route handlers and DB calls; use `asyncpg` directly (not SQLAlchemy)
- **No print statements** — use Python `logging` module with structured log lines
- **Ruff for linting** — `ruff check .` must pass before commit
- **mypy for type checking** — `mypy app/` must pass
- **Meaningful commits** — write commits as: `feat: add hybrid search with RRF merge` not `fix stuff`

## Implementation Milestones

1. **M1 — Scaffold** ✅ (documents written, repo initialised)
   - Project structure, `CLAUDE.md`, `PROPOSAL.md`, `README.md`, `.env.example`, `.gitignore`

2. **M2 — Database layer**
   - `app/db/connection.py` — asyncpg pool with lifespan
   - `app/db/schema.py` — DDL execution on startup
   - `docker-compose.yml` with `pgvector/pgvector:pg16` image

3. **M3 — Ingest pipeline**
   - `app/services/chunker.py` — markdown-aware splitter
   - `app/services/embedder.py` — Ollama + Anthropic implementations
   - `app/api/ingest.py` — `/ingest` endpoint
   - `corpus/fintech-demo/` — 7 demo markdown files

4. **M4 — Query & streaming**
   - `app/services/retriever.py` — hybrid search + RRF
   - `app/services/llm.py` — streaming LLM abstraction
   - `app/api/query.py` — `/query` SSE endpoint

5. **M5 — Tests & CI**
   - `tests/` — unit tests for chunker, retriever, API
   - `.github/workflows/ci.yml` — ruff + mypy + pytest

6. **M6 — Docker & deploy**
   - `Dockerfile` + `docker-compose.yml`
   - Render deployment configuration
   - Update `README.md` with live demo URL

## Demo Corpus

`corpus/fintech-demo/` contains fictional documentation for a payment platform called **FinPay**. It is intentionally written to mirror real fintech architecture patterns without referencing any real company, system, or integration.

Do not add any content referencing Singtel, DASH, WU, Thunes, Tranglo, or any real partner or internal system name.

## Workspace Registration

- **Port:** `8001` — registered in `workspace-app-registry.md`
- **Repo path:** `/Users/ThanhNguyen/AI_WS/docu-rag`
- **Design identity:** Stacked pages with a spark in the top-right corner · Teal `#0f766e`
- **Favicon:** `public/favicon.svg` (SVG, teal, create at M6)

---

## zshrc Aliases

Add these to `~/.zshrc` at workspace registration time (after `docker compose` is confirmed working):

```zsh
# docu-rag
DOCU_RAG_DIR="/Users/ThanhNguyen/AI_WS/docu-rag"
DOCU_RAG_PORT=8001

docu-rag-start() {
  echo "Starting docu-rag..."
  (cd "$DOCU_RAG_DIR" && docker compose up --build -d > /tmp/docu-rag.log 2>&1)
  echo "docu-rag started — http://localhost:$DOCU_RAG_PORT"
  echo "Logs: docu-rag-logs"
}

docu-rag-stop() {
  echo "Stopping docu-rag..."
  (cd "$DOCU_RAG_DIR" && docker compose down)
  echo "Stopped."
}

docu-rag-restart() { docu-rag-stop && sleep 1 && docu-rag-start; }
docu-rag-logs()    { tail -f /tmp/docu-rag.log; }

docu-rag-status() {
  lsof -ti tcp:$DOCU_RAG_PORT > /dev/null 2>&1 \
    && echo "docu-rag is RUNNING — http://localhost:$DOCU_RAG_PORT" \
    || echo "docu-rag is STOPPED"
}
```

After editing `~/.zshrc`: `source ~/.zshrc`

---

## Decision Records

Non-trivial architecture decisions are tracked under `docs/decisions/`:

```
docs/decisions/
  ADR-001-tech-stack.md
  ADR-002-hybrid-search-rrf.md
  ADR-003-ollama-default-provider.md
  PDR-001-mvp-scope.md
```

**ADR template:**
```markdown
# ADR-NNN — <Short Title>
**Date**: YYYY-MM-DD
**Status**: Proposed | Accepted | Superseded by ADR-NNN

## Context
## Decision
## Consequences
- Good:
- Trade-off:
- Risk:
```

The first session should create `ADR-001-tech-stack.md` (FastAPI + pgvector + Ollama decisions) and `PDR-001-mvp-scope.md` (what is in/out of scope for v1) as part of M1 scaffold.

---

## Claude Code Permissions

`.claude/settings.local.json` is already configured with permissions for all Python/Docker commands used in this project. If a new tool is needed, add a category wildcard entry — do not approve per-command exact paths.

---

## Portfolio Notes

- This repo is public. Every commit is visible to hiring managers.
- README must always have a working quick-start and an architecture diagram.
- The live demo URL (Render) must be in the README before sharing in interviews.
- MIT license is applied. Do not add any proprietary code or data.
