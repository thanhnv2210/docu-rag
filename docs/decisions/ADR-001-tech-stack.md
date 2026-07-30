# ADR-001 — Tech Stack Selection
**Date**: 2026-07-30
**Status**: Accepted

## Context

docu-rag is a portfolio project targeting AI engineer roles. It must demonstrate production-grade backend skills (async Python, PostgreSQL, streaming APIs) while remaining easy for anyone to clone and run locally without external accounts or paid services.

Key constraints:
- Zero-cost local development with no API keys required
- Demonstrates hybrid search (not just vector search)
- Public repo — every dependency choice is visible to hiring reviewers
- Deployable on Render free tier for a live demo

## Decision

| Concern | Choice |
|---|---|
| Web framework | **FastAPI** — native async, Pydantic v2 integration, auto OpenAPI docs, first-class SSE |
| Vector store | **pgvector** on PostgreSQL 16 — avoids a second infrastructure dependency; SQL-native hybrid search |
| Default LLM + embeddings | **Ollama** (`llama3.2` + `nomic-embed-text`) — fully offline, zero cost, zero API key |
| Optional provider | **Anthropic** (`claude-haiku-4-5-20251001` + `voyage-3`) — demonstrates cloud LLM integration |
| DB driver | **asyncpg** — fastest async PostgreSQL driver; no ORM overhead needed for this schema |
| Orchestration | **LangChain** for chunking only (`RecursiveCharacterTextSplitter`) — retrieval is written directly in SQL |
| Validation | **Pydantic v2** — FastAPI-native; 10–20× faster than v1 |
| Config | **pydantic-settings** — reads `.env`, type-safe, integrates with FastAPI dependency injection |
| Containerisation | **Docker Compose** — single command to start API + PostgreSQL |
| CI | **GitHub Actions** — free for public repos; ruff + mypy + pytest pipeline |
| Deployment | **Render** — free tier supports Docker services; no credit card needed |

## Consequences

**Good:**
- Anyone can `git clone && docker compose up` and have a running system in minutes.
- pgvector keeps the infrastructure footprint minimal — one database, not two services.
- Staying off LangChain for retrieval means the SQL logic is fully transparent and easy to explain in interviews.
- Pluggable provider pattern makes it trivial to add future providers (OpenAI, Cohere, etc.).

**Trade-off:**
- asyncpg requires raw SQL rather than an ORM. Acceptable here — the schema is small and explicit SQL is a feature, not a liability.
- Ollama requires ~4 GB RAM for `llama3.2`. Not suitable for low-memory environments, but any developer machine handles it.

**Risk:**
- Render free tier PostgreSQL instances expire after 90 days. Mitigation: use pgvector as a Docker service on Render rather than the managed DB, or document the limitation in the README.
