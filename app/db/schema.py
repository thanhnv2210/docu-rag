import logging

import asyncpg

logger = logging.getLogger(__name__)

# DDL is parameterised by embed_dims at runtime so switching providers
# (nomic-embed-text=768, voyage-3=1536) just requires a re-ingest, not
# a code change.

_CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector;"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS documents (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path   TEXT        NOT NULL,
    title       TEXT,
    chunk_index INTEGER     NOT NULL,
    content     TEXT        NOT NULL,
    embedding   VECTOR({dims}),
    metadata    JSONB       DEFAULT '{{}}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
"""

# GIN index for full-text search — safe to create on an empty table.
_CREATE_FTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_documents_fts
    ON documents USING gin(to_tsvector('english', content));
"""

# IVFFlat index for vector search.
# NOTE: IVFFlat requires rows to build cluster centroids — creating it on an
# empty table produces a useless index (lists=1). We create it here with
# IF NOT EXISTS so it is a no-op on subsequent startups; the ingest endpoint
# calls ensure_vector_index() after bulk insert to (re)build it with data.
_CREATE_VECTOR_INDEX = """
CREATE INDEX IF NOT EXISTS idx_documents_embedding
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
"""

# Called by the ingest service after rows are inserted so IVFFlat has data.
_DROP_VECTOR_INDEX = "DROP INDEX IF EXISTS idx_documents_embedding;"


async def init_schema(conn: asyncpg.Connection, embed_dims: int) -> None:
    """Run DDL on startup. Safe to call multiple times (all statements are idempotent)."""
    await conn.execute(_CREATE_EXTENSION)
    await conn.execute(_CREATE_TABLE.format(dims=embed_dims))
    await conn.execute(_CREATE_FTS_INDEX)
    logger.info("Database schema initialised (embed_dims=%d)", embed_dims)


async def ensure_vector_index(conn: asyncpg.Connection) -> None:
    """Drop and recreate the IVFFlat index so it is built over actual data.

    Called by the ingest endpoint after a bulk insert. The DROP + CREATE
    approach ensures the index reflects the current data distribution.
    """
    await conn.execute(_DROP_VECTOR_INDEX)
    await conn.execute(_CREATE_VECTOR_INDEX)
    logger.info("IVFFlat vector index rebuilt")
