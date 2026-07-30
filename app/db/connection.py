import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg

logger = logging.getLogger(__name__)


async def create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    logger.info("Database pool created")
    return pool  # type: ignore[return-value]


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()
    logger.info("Database pool closed")


@asynccontextmanager
async def acquire(pool: asyncpg.Pool) -> AsyncGenerator[asyncpg.Connection, None]:
    async with pool.acquire() as conn:
        yield conn  # type: ignore[misc]
