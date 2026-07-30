"""
Integration tests for /health, /ingest, and /query endpoints.

Requires DATABASE_URL to be set (a live pgvector PostgreSQL instance).
All tests are skipped automatically when DATABASE_URL is not available.
Embedder and LLM are mocked — no Ollama or Anthropic API key needed.
"""

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from tests.conftest import skip_without_db


def _write_md(path: Path, name: str, content: str) -> None:
    (path / name).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@skip_without_db
async def test_health_returns_200(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200


@skip_without_db
async def test_health_response_shape(client: AsyncClient) -> None:
    data = (await client.get("/health")).json()
    assert "status" in data
    assert "vector_count" in data
    assert "provider" in data
    assert "embed_model" in data
    assert "llm_model" in data
    assert data["status"] == "ok"


@skip_without_db
async def test_health_vector_count_is_int(client: AsyncClient) -> None:
    data = (await client.get("/health")).json()
    assert isinstance(data["vector_count"], int)
    assert data["vector_count"] >= 0


# ---------------------------------------------------------------------------
# POST /ingest
# ---------------------------------------------------------------------------

@skip_without_db
async def test_ingest_success(client: AsyncClient, tmp_path: Path) -> None:
    _write_md(tmp_path, "overview.md", "# Overview\n\nThis is the system overview.\n")
    _write_md(tmp_path, "ops.md", "# Operations\n\nDeployment and runbook details.\n")

    response = await client.post(
        "/ingest",
        json={"corpus_path": str(tmp_path), "reset": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["chunks_indexed"] > 0
    assert data["files_processed"] == 2
    assert data["duration_ms"] >= 0


@skip_without_db
async def test_ingest_response_schema(client: AsyncClient, tmp_path: Path) -> None:
    _write_md(tmp_path, "doc.md", "# Doc\n\nContent.\n")
    data = (
        await client.post("/ingest", json={"corpus_path": str(tmp_path), "reset": True})
    ).json()
    assert set(data.keys()) == {"chunks_indexed", "files_processed", "duration_ms"}


@skip_without_db
async def test_ingest_invalid_path_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        "/ingest",
        json={"corpus_path": "/nonexistent/path/that/does/not/exist"},
    )
    assert response.status_code == 422


@skip_without_db
async def test_ingest_empty_dir_returns_422(client: AsyncClient, tmp_path: Path) -> None:
    response = await client.post(
        "/ingest",
        json={"corpus_path": str(tmp_path)},
    )
    assert response.status_code == 422


@skip_without_db
async def test_ingest_reset_true_replaces_chunks(client: AsyncClient, tmp_path: Path) -> None:
    _write_md(tmp_path, "a.md", "# A\n\nFirst version.\n")

    r1 = (await client.post("/ingest", json={"corpus_path": str(tmp_path), "reset": True})).json()
    count_after_first = r1["chunks_indexed"]

    r2 = (await client.post("/ingest", json={"corpus_path": str(tmp_path), "reset": True})).json()
    count_after_reset = r2["chunks_indexed"]

    # After a reset re-ingest of the same corpus, count should be stable
    assert count_after_reset == count_after_first


# ---------------------------------------------------------------------------
# POST /query
# ---------------------------------------------------------------------------

@skip_without_db
async def test_query_returns_event_stream(client: AsyncClient, tmp_path: Path) -> None:
    _write_md(tmp_path, "q.md", "# Retry Policy\n\nUse exponential backoff for retries.\n")
    await client.post("/ingest", json={"corpus_path": str(tmp_path), "reset": True})

    response = await client.post(
        "/query",
        json={"question": "What is the retry policy?", "top_k": 2},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]


@skip_without_db
async def test_query_stream_contains_token_events(client: AsyncClient, tmp_path: Path) -> None:
    _write_md(tmp_path, "retry.md", "# Retry\n\nExponential backoff is used.\n")
    await client.post("/ingest", json={"corpus_path": str(tmp_path), "reset": True})

    response = await client.post(
        "/query",
        json={"question": "How does retry work?", "top_k": 2},
    )
    raw = response.text
    events = [
        json.loads(line[len("data: "):])
        for line in raw.splitlines()
        if line.startswith("data: ") and not line.startswith("data: [DONE]")
    ]
    token_events = [e for e in events if e.get("type") == "token"]
    assert len(token_events) > 0


@skip_without_db
async def test_query_stream_contains_metadata_event(client: AsyncClient, tmp_path: Path) -> None:
    _write_md(tmp_path, "meta.md", "# Meta\n\nSome content.\n")
    await client.post("/ingest", json={"corpus_path": str(tmp_path), "reset": True})

    response = await client.post(
        "/query",
        json={"question": "Tell me about meta.", "top_k": 2},
    )
    raw = response.text
    events = [
        json.loads(line[len("data: "):])
        for line in raw.splitlines()
        if line.startswith("data: ") and not line.startswith("data: [DONE]")
    ]
    metadata_events = [e for e in events if e.get("type") == "metadata"]
    assert len(metadata_events) == 1
    meta = metadata_events[0]
    assert "sources" in meta
    assert "latency_ms" in meta
    assert "tokens_used" in meta
    assert "provider" in meta


@skip_without_db
async def test_query_stream_ends_with_done(client: AsyncClient, tmp_path: Path) -> None:
    _write_md(tmp_path, "done.md", "# Done\n\nContent.\n")
    await client.post("/ingest", json={"corpus_path": str(tmp_path), "reset": True})

    response = await client.post(
        "/query",
        json={"question": "Anything?", "top_k": 1},
    )
    assert response.text.strip().endswith("data: [DONE]")


@skip_without_db
async def test_query_missing_question_returns_422(client: AsyncClient) -> None:
    response = await client.post("/query", json={"top_k": 5})
    assert response.status_code == 422
