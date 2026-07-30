import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.db.schema import ensure_vector_index
from app.models.schemas import IngestRequest, IngestResponse
from app.services.chunker import chunk_corpus
from app.services.retriever import embedding_to_pg

logger = logging.getLogger(__name__)

router = APIRouter()

_INSERT_SQL = """
INSERT INTO documents (file_path, title, chunk_index, content, embedding, metadata)
VALUES ($1, $2, $3, $4, $5::vector, $6::jsonb)
"""

_DELETE_CORPUS_SQL = """
DELETE FROM documents WHERE file_path LIKE $1
"""


@router.post("/ingest", response_model=IngestResponse)
async def ingest(request: Request, body: IngestRequest) -> IngestResponse:
    started_at = time.monotonic()

    corpus_path = Path(body.corpus_path)
    if not corpus_path.exists() or not corpus_path.is_dir():
        raise HTTPException(status_code=422, detail=f"corpus_path '{body.corpus_path}' does not exist or is not a directory")

    pool = request.app.state.pool
    embedder = request.app.state.embedder
    settings = request.app.state.settings

    # 1. Chunk all markdown files
    chunks = chunk_corpus(
        corpus_path,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    if not chunks:
        raise HTTPException(status_code=422, detail="No markdown files found in corpus_path")

    files_processed = len({c.file_path for c in chunks})

    # 2. Embed all chunk texts in batches
    texts = [c.content for c in chunks]
    vectors = await embedder.embed(texts)

    # 3. Persist to DB
    async with pool.acquire() as conn:
        async with conn.transaction():
            if body.reset:
                prefix = str(corpus_path).rstrip("/") + "%"
                deleted = await conn.execute(_DELETE_CORPUS_SQL, prefix)
                logger.info("Reset: deleted existing rows matching '%s' (%s)", prefix, deleted)

            rows = [
                (
                    chunk.file_path,
                    chunk.title,
                    chunk.chunk_index,
                    chunk.content,
                    embedding_to_pg(vectors[i]),
                    json.dumps(chunk.metadata),
                )
                for i, chunk in enumerate(chunks)
            ]
            await conn.executemany(_INSERT_SQL, rows)

        # 4. Rebuild IVFFlat index outside the transaction (DDL cannot run inside)
        await ensure_vector_index(conn)

    duration_ms = int((time.monotonic() - started_at) * 1000)
    logger.info(
        "Ingest complete: %d chunks from %d files in %d ms",
        len(chunks),
        files_processed,
        duration_ms,
    )

    return IngestResponse(
        chunks_indexed=len(chunks),
        files_processed=files_processed,
        duration_ms=duration_ms,
    )
