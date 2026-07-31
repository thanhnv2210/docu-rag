# PDR-001 — MVP Scope
**Date**: 2026-07-30
**Status**: Accepted

## Context

docu-rag is scoped as a v1 portfolio project with a fixed delivery milestone. We need a clear boundary between what ships in v1 and what is explicitly deferred so that scope creep doesn't delay the live demo.

## Decision

### In scope (v1 MVP)

| Feature | Rationale |
|---|---|
| Ingest markdown folders via `POST /ingest` | Core use case |
| Hybrid search: pgvector cosine + PostgreSQL FTS + RRF merge | Key differentiator; interview talking point |
| Streaming SSE answers via `POST /query` | Production-grade UX pattern |
| Source attribution (file + chunk) in every response | Grounds the answer; required for trustworthy RAG |
| Ollama as default provider (offline, zero cost) | Enables `git clone && run` for any reviewer |
| Anthropic as optional provider | Demonstrates cloud LLM integration |
| `GET /health` with vector count | Minimal operational observability |
| Demo corpus (`corpus/fintech-demo/`, 7 files) | Reviewers can query without their own docs |
| Docker Compose setup | One-command local environment |
| GitHub Actions CI (ruff + mypy + pytest) | Professional quality signal |
| Render deployment with live demo URL | Portfolio credibility |
| Static HTML UI (`GET /`) | Makes live demo accessible to non-technical interviewers without curl |
| HNSW vector index | Required after IVFFlat exceeded Render free-tier `maintenance_work_mem`; better recall for small-to-medium datasets |

### Out of scope (v1)

| Feature | Reason deferred |
|---|---|
| Authentication / API keys | No multi-user requirement; adds complexity with no portfolio value at this stage |
| Multi-user sessions / conversation history | RAG is stateless by design in v1 |
| Document update / delete | Full re-ingest (`reset=true`) is sufficient for v1 corpus sizes |
| PDF / DOCX ingestion | Requires format-specific parsers; markdown is sufficient for demo |
| Streaming progress for ingest | Nice-to-have; synchronous JSON response is adequate for v1 corpus sizes |
| Rate limiting / abuse protection | Not needed for a portfolio demo with no public traffic |

## Consequences

**Good:**
- Tight scope means M1–M6 can be completed in a single focused build sprint.
- Every included feature directly demonstrates a skill relevant to AI engineer roles.

**Trade-off:**
- Static HTML UI is intentionally minimal (no chat history, no markdown diffing during stream). Sufficient for a portfolio demo; a full React frontend would add build tooling complexity with limited portfolio value at this stage.

**Risk:**
- Render free tier limits may affect the live demo. Mitigation: document clearly and provide Docker instructions as the primary local path.
