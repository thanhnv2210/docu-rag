import logging
from dataclasses import dataclass

import asyncpg

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant — k=60 is the standard value from the
# original RRF paper (Cormack et al., 2009). Higher k reduces the influence
# of top-ranked documents; 60 is a well-calibrated default.
_RRF_K = 60

_HYBRID_SEARCH_SQL = """
WITH vector_results AS (
    SELECT
        id,
        content,
        file_path,
        title,
        chunk_index,
        ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS vector_rank
    FROM documents
    ORDER BY embedding <=> $1::vector
    LIMIT $2
),
fts_results AS (
    SELECT
        id,
        content,
        file_path,
        title,
        chunk_index,
        ROW_NUMBER() OVER (
            ORDER BY ts_rank(to_tsvector('english', content), query) DESC
        ) AS fts_rank
    FROM documents, plainto_tsquery('english', $3) AS query
    WHERE to_tsvector('english', content) @@ query
    ORDER BY ts_rank(to_tsvector('english', content), query) DESC
    LIMIT $2
),
rrf AS (
    SELECT
        COALESCE(v.id,          f.id)          AS id,
        COALESCE(v.content,     f.content)     AS content,
        COALESCE(v.file_path,   f.file_path)   AS file_path,
        COALESCE(v.title,       f.title)       AS title,
        COALESCE(v.chunk_index, f.chunk_index) AS chunk_index,
        COALESCE(1.0 / ({k} + v.vector_rank), 0) +
        COALESCE(1.0 / ({k} + f.fts_rank),   0) AS rrf_score
    FROM vector_results v
    FULL OUTER JOIN fts_results f ON v.id = f.id
)
SELECT id, content, file_path, title, chunk_index, rrf_score
FROM rrf
ORDER BY rrf_score DESC
LIMIT $4
""".format(k=_RRF_K)


@dataclass
class RetrievedChunk:
    id: str
    content: str
    file_path: str
    title: str | None
    chunk_index: int
    rrf_score: float


def _embedding_to_pg(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


async def retrieve(
    pool: asyncpg.Pool,
    query_vector: list[float],
    question: str,
    top_k: int,
) -> list[RetrievedChunk]:
    """Run hybrid vector + FTS search and return top_k chunks via RRF merge."""
    candidate_k = top_k * 2  # oversample before RRF merge

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _HYBRID_SEARCH_SQL,
            _embedding_to_pg(query_vector),
            candidate_k,
            question,
            top_k,
        )

    chunks = [
        RetrievedChunk(
            id=str(row["id"]),
            content=row["content"],
            file_path=row["file_path"],
            title=row["title"],
            chunk_index=row["chunk_index"],
            rrf_score=float(row["rrf_score"]),
        )
        for row in rows
    ]

    logger.debug(
        "Retrieved %d chunks for question=%r (top_k=%d)",
        len(chunks),
        question[:60],
        top_k,
    )
    return chunks
