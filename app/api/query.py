import json
import logging
import time
from typing import AsyncGenerator

import tiktoken
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.models.schemas import ChunkSource, QueryRequest
from app.services.llm import _SYSTEM_PROMPT
from app.services.retriever import RetrievedChunk, retrieve

logger = logging.getLogger(__name__)

router = APIRouter()

_tokenizer = tiktoken.get_encoding("cl100k_base")


def _build_user_message(question: str, chunks: list[RetrievedChunk]) -> str:
    context_blocks = []
    for chunk in chunks:
        source_label = f"[{chunk.file_path}, chunk {chunk.chunk_index}]"
        context_blocks.append(f"{source_label}\n{chunk.content}")
    context = "\n\n---\n\n".join(context_blocks)
    return f"Context:\n\n{context}\n\nQuestion: {question}"


async def _sse_generator(
    question: str,
    chunks: list[RetrievedChunk],
    request: Request,
    started_at: float,
) -> AsyncGenerator[str, None]:
    llm = request.app.state.llm
    settings = request.app.state.settings

    messages = [{"role": "user", "content": _build_user_message(question, chunks)}]

    accumulated = ""
    async for token in llm.stream_chat(messages, system=_SYSTEM_PROMPT):
        accumulated += token
        payload = json.dumps({"type": "token", "content": token})
        yield f"data: {payload}\n\n"

    tokens_used = len(_tokenizer.encode(accumulated))
    latency_ms = int((time.monotonic() - started_at) * 1000)

    sources = [
        ChunkSource(
            file_path=c.file_path,
            chunk_index=c.chunk_index,
            title=c.title,
        )
        for c in chunks
    ]

    metadata_payload = json.dumps(
        {
            "type": "metadata",
            "sources": [s.model_dump() for s in sources],
            "latency_ms": latency_ms,
            "tokens_used": tokens_used,
            "provider": settings.llm_provider,
        }
    )
    yield f"data: {metadata_payload}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/query")
async def query(request: Request, body: QueryRequest) -> StreamingResponse:
    started_at = time.monotonic()

    embedder = request.app.state.embedder
    pool = request.app.state.pool
    settings = request.app.state.settings

    top_k = body.top_k if body.top_k is not None else settings.top_k

    # 1. Embed the question
    [query_vector] = await embedder.embed([body.question])

    # 2. Retrieve relevant chunks via hybrid search
    chunks = await retrieve(pool, query_vector, body.question, top_k)

    if not chunks:
        logger.warning("No chunks retrieved for question=%r", body.question[:80])

    logger.info(
        "Query: question=%r | retrieved=%d | provider=%s",
        body.question[:80],
        len(chunks),
        settings.llm_provider,
    )

    # 3. Stream SSE response
    return StreamingResponse(
        _sse_generator(body.question, chunks, request, started_at),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )
