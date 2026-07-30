"""Unit tests for app/services/retriever.py — no external services required."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.retriever import RetrievedChunk, embedding_to_pg, retrieve

# ---------------------------------------------------------------------------
# embedding_to_pg — pure function
# ---------------------------------------------------------------------------

def test_embedding_to_pg_format() -> None:
    vec = [1.0, -0.5, 0.123456789]
    result = embedding_to_pg(vec)
    assert result.startswith("[")
    assert result.endswith("]")
    assert result.count(",") == 2


def test_embedding_to_pg_precision() -> None:
    vec = [1.0 / 3.0]
    result = embedding_to_pg(vec)
    # Should use 8 decimal places
    assert "0.33333333" in result


def test_embedding_to_pg_single_element() -> None:
    result = embedding_to_pg([0.5])
    assert result == "[0.50000000]"


def test_embedding_to_pg_zeros() -> None:
    result = embedding_to_pg([0.0, 0.0, 0.0])
    assert result == "[0.00000000,0.00000000,0.00000000]"


# ---------------------------------------------------------------------------
# RRF score ordering
# ---------------------------------------------------------------------------

def _rrf_score(vector_rank: int | None, fts_rank: int | None, k: int = 60) -> float:
    """Mirror the SQL RRF formula for unit-testing expected sort order."""
    score = 0.0
    if vector_rank is not None:
        score += 1.0 / (k + vector_rank)
    if fts_rank is not None:
        score += 1.0 / (k + fts_rank)
    return score


def test_rrf_both_ranks_beats_single_rank() -> None:
    score_both = _rrf_score(1, 1)
    score_vector_only = _rrf_score(1, None)
    score_fts_only = _rrf_score(None, 1)
    assert score_both > score_vector_only
    assert score_both > score_fts_only


def test_rrf_lower_rank_number_is_better() -> None:
    assert _rrf_score(1, None) > _rrf_score(2, None)
    assert _rrf_score(None, 1) > _rrf_score(None, 10)


def test_rrf_symmetric() -> None:
    assert _rrf_score(3, 5) == _rrf_score(5, 3)


def test_rrf_k_constant_dampens_top_rank_advantage() -> None:
    # With larger k, the difference between rank 1 and rank 2 shrinks
    diff_small_k = _rrf_score(1, None, k=1) - _rrf_score(2, None, k=1)
    diff_large_k = _rrf_score(1, None, k=100) - _rrf_score(2, None, k=100)
    assert diff_small_k > diff_large_k


# ---------------------------------------------------------------------------
# retrieve() — mocked asyncpg pool
# ---------------------------------------------------------------------------

def _make_row(
    id_: str,
    content: str,
    file_path: str,
    title: str | None,
    chunk_index: int,
    rrf_score: float,
) -> dict:
    return {
        "id": id_,
        "content": content,
        "file_path": file_path,
        "title": title,
        "chunk_index": chunk_index,
        "rrf_score": rrf_score,
    }


async def test_retrieve_returns_retrieved_chunks() -> None:
    fake_rows = [
        _make_row("id-1", "Payment state machine", "tx.md", "States", 0, 0.032),
        _make_row("id-2", "Retry policy details", "resilience.md", "Retry", 1, 0.016),
    ]

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=fake_rows)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))

    query_vector = [0.1] * 768
    results = await retrieve(mock_pool, query_vector, "payment failure", top_k=2)

    assert len(results) == 2
    assert all(isinstance(r, RetrievedChunk) for r in results)
    assert results[0].id == "id-1"
    assert results[0].file_path == "tx.md"
    assert results[0].title == "States"
    assert results[1].rrf_score == pytest.approx(0.016)


async def test_retrieve_passes_correct_params_to_db() -> None:
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))

    query_vector = [0.0] * 768
    await retrieve(mock_pool, query_vector, "test question", top_k=3)

    call_args = mock_conn.fetch.call_args
    _sql, vec_str, candidate_k, question, final_k = call_args.args

    assert vec_str == embedding_to_pg(query_vector)
    assert candidate_k == 6          # top_k * 2
    assert question == "test question"
    assert final_k == 3


async def test_retrieve_empty_result_returns_empty_list() -> None:
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))

    results = await retrieve(mock_pool, [0.0] * 768, "anything", top_k=5)
    assert results == []
