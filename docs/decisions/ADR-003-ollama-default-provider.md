# ADR-003 — Ollama as the Default LLM and Embedding Provider
**Date**: 2026-07-31
**Status**: Accepted

## Context

docu-rag needs an LLM for answer generation and an embedding model for vector indexing. The choice of provider affects:

- **Local development experience** — does `git clone && docker compose up` actually work without external accounts?
- **Cost** — a portfolio project has no budget; API costs must be zero for normal use.
- **Portability** — the project should be demonstrable offline (a laptop, a conference Wi-Fi that blocks certain APIs, etc.).
- **Interview credibility** — the choice signals understanding of the LLM ecosystem beyond "call the OpenAI API".

The two realistic options at the time of design were cloud API providers (OpenAI, Anthropic, Cohere) and local inference via Ollama.

## Decision

**Ollama is the default provider.** A cloud provider (Anthropic) is supported as an optional override via `LLM_PROVIDER=anthropic`.

| Concern | Ollama (default) | Anthropic (override) |
|---|---|---|
| API key required | No | Yes |
| Cost | Zero | Per-token (free tier available) |
| Works offline | Yes | No |
| LLM model | `llama3.2` (3B) | `claude-haiku-4-5-20251001` |
| Embedding model | `nomic-embed-text` (768 dims) | `voyage-3` (1024 dims) |
| Answer quality | Good | Better |
| Runs on Render | No | Yes |

**Why `llama3.2` (3B)?** It fits in 4–6 GB of RAM, runs at acceptable speed on CPU, and produces coherent answers for narrow-domain RAG (the model doesn't need to know everything — it only needs to synthesise the retrieved context). Larger models (`llama3.1:8b`, `llama3.3:70b`) give better answers but are impractical as a default for developers on laptops.

**Why `nomic-embed-text`?** It is the most widely used open embedding model in the Ollama ecosystem, produces 768-dim vectors, and achieves competitive MTEB scores for retrieval tasks. It requires no API key and no internet connection after initial `ollama pull`.

**Why Anthropic as the cloud override?** `claude-haiku-4-5-20251001` is fast and low-cost while producing noticeably better answers than `llama3.2`. `voyage-3` (Voyage AI, not Anthropic directly) is one of the top-performing embedding models on the MTEB leaderboard. The combination gives a meaningful quality uplift for the live demo without being expensive. The override also demonstrates that the pluggable provider abstraction (`LLMClient` ABC, `Embedder` ABC) works as designed.

**Why not OpenAI?** OpenAI is the obvious choice but every RAG tutorial uses it. Choosing Ollama-first with an Anthropic override signals intentional decision-making rather than defaulting to convention.

## Consequences

**Good:**
- Zero-friction local development — `ollama pull llama3.2 && ollama pull nomic-embed-text` is the only prerequisite.
- The live Render demo runs on Anthropic, demonstrating the pluggable provider switch works in production.
- Switching providers is a one-line `.env` change (`LLM_PROVIDER=anthropic`) followed by a re-ingest (embedding dimensions change from 768 to 1024).
- Adding a third provider (OpenAI, Cohere, Google) requires implementing two methods: `embed(texts)` and `stream_chat(messages)`.

**Trade-off:**
- Ollama requires ~4 GB RAM for `llama3.2`. Developers on low-memory machines (≤8 GB) may experience slowness or swap pressure. Mitigation: document the requirement clearly and let users switch to `llama3.2:1b` in `.env`.
- Answer quality on Ollama is noticeably lower than Anthropic for complex multi-hop queries. The live demo using Anthropic covers this gap for interview presentations.

**Risk:**
- Ollama model availability changes over time. If `llama3.2` is deprecated from the Ollama registry, `OLLAMA_LLM_MODEL` in `.env` must be updated. The abstraction insulates the codebase from this change.
