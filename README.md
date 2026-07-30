# docu-rag

A production-grade RAG (Retrieval-Augmented Generation) API. Point it at any folder of markdown documents and ask questions in plain English — it returns grounded, streaming answers with source attribution.

Built with **Python · FastAPI · LangChain · pgvector · Ollama**.

---

## Features

- **Hybrid search** — combines vector similarity (pgvector) and full-text search (`ts_rank`) via Reciprocal Rank Fusion (RRF) for higher recall than either method alone
- **Streaming responses** — answers stream token-by-token via SSE; no waiting for the full response
- **Source attribution** — every answer includes the exact chunks and files used to generate it
- **Works offline** — defaults to [Ollama](https://ollama.ai) (local LLM + embeddings); no API key required
- **Pluggable corpus** — ingest any markdown folder via a single API call
- **Anthropic API** — optional override for higher-quality responses (`claude-haiku-4-5`)

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
- [Ollama](https://ollama.ai) running locally

```bash
# Pull required Ollama models
ollama pull llama3.2
ollama pull nomic-embed-text
```

### Run

```bash
git clone https://github.com/<your-username>/docu-rag.git
cd docu-rag
cp .env.example .env
docker compose up --build
```

API is available at `http://localhost:8001`. OpenAPI docs at `http://localhost:8001/docs`.

### Ingest the demo corpus

```bash
curl -X POST http://localhost:8001/ingest \
  -H "Content-Type: application/json" \
  -d '{"corpus_path": "corpus/fintech-demo", "reset": true}'
```

### Ask a question

```bash
curl -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -N \
  -d '{"question": "How does the system handle a payment timeout?", "top_k": 5}'
```

Response streams as SSE:
```
data: {"type": "token", "content": "When a payment times"}
data: {"type": "token", "content": " out, the transaction"}
...
data: {"type": "metadata", "sources": [...], "latency_ms": 980, "tokens_used": 312}
data: [DONE]
```

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/ingest` | POST | Index a markdown corpus into pgvector |
| `/query` | POST | Ask a question; streams SSE response |
| `/health` | GET | Health check + vector count |
| `/docs` | GET | Interactive OpenAPI UI |

See `PROPOSAL.md` for full request/response schemas.

---

## Architecture

```
Client (curl / React)
        │
        ▼
    FastAPI
   /ingest    /query (SSE)    /health
        │          │
   Chunker    Retriever
  (LangChain) (Hybrid SQL: vector + FTS → RRF)
        │          │
   Embedder    LLM Client
   (Ollama or Anthropic)
        │
   PostgreSQL 16 + pgvector
   ├── VECTOR index (IVFFlat)
   └── FTS index (GIN)
```

### How hybrid search works

1. Embed the question → query vector
2. Run two searches in parallel:
   - **Vector search** — cosine distance (`embedding <=> query_vector`)
   - **Keyword search** — full-text rank (`ts_rank`)
3. Merge results using **Reciprocal Rank Fusion (RRF)**: `score = 1/(60 + rank)`
4. Return top-k chunks to the LLM as context

---

## Configuration

Copy `.env.example` to `.env` and edit as needed.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | (required) | asyncpg connection string |
| `LLM_PROVIDER` | `ollama` | `ollama` or `anthropic` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_LLM_MODEL` | `llama3.2` | Chat model |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model (768 dims) |
| `ANTHROPIC_API_KEY` | _(optional)_ | Required if `LLM_PROVIDER=anthropic` |
| `CHUNK_SIZE` | `512` | Max tokens per chunk |
| `TOP_K` | `5` | Chunks retrieved per query |

### Use Anthropic instead of Ollama

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
EMBED_DIMS=1536
```

---

## Demo Corpus

`corpus/fintech-demo/` ships with the repo — fictional documentation for a payment platform called **FinPay**. It covers system architecture, transaction state machine, payment hub integrations, database schema, API reference, resilience patterns, and deployment runbooks.

Try these queries after ingesting:
- `"What retry policy applies when ArcaPay returns a timeout?"`
- `"Walk me through the transaction lifecycle from initiation to settlement"`
- `"What indexes exist on the transactions table and why?"`

---

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Lint
ruff check .

# Type check
mypy app/
```

---

## Project Structure

```
app/
├── main.py          # FastAPI app + lifespan
├── config.py        # Settings (pydantic-settings)
├── api/             # Route handlers
├── services/        # Chunker, Embedder, Retriever, LLMClient
├── db/              # asyncpg pool + DDL
└── models/          # Pydantic v2 schemas
corpus/
└── fintech-demo/    # Demo markdown corpus
tests/               # pytest test suite
```

---

## License

MIT
