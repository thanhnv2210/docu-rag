# ADR-002 — Hybrid Search with Reciprocal Rank Fusion
**Date**: 2026-07-31
**Status**: Accepted

## Context

RAG systems need to retrieve the most relevant document chunks for a given query. The naive approach is vector similarity search alone: embed the query, find the nearest neighbours in the vector space. This works well for semantic matches ("what does exponential backoff mean?") but fails for exact keyword matches, rare proper nouns, and domain-specific identifiers (error codes, field names, API endpoint paths).

In the fintech demo corpus, queries like "What happens when ArcaPay returns TIMEOUT?" depend on matching the exact string `TIMEOUT` and the hub name `ArcaPay`. A pure vector search may rank semantically similar paragraphs higher than the paragraph that literally contains those tokens.

The alternative — keyword-only full-text search — has the inverse problem: it misses paraphrased content and synonyms.

A further constraint: this is a single-PostgreSQL deployment. Adding a dedicated search engine (Elasticsearch, OpenSearch) would double the infrastructure footprint and complicate local development.

## Decision

Run two searches in parallel against the same `documents` table and merge the results with **Reciprocal Rank Fusion (RRF)**:

1. **Vector search** — cosine distance via pgvector (`embedding <=> query_vector`), top 2× `top_k` candidates.
2. **Keyword search** — PostgreSQL full-text rank (`ts_rank` on a GIN-indexed `tsvector`), top 2× `top_k` candidates.
3. **RRF merge** — for each candidate, sum `1 / (60 + rank)` across both lists. Chunks appearing in both lists receive a boost. Take the top `top_k` by merged score.

```python
# RRF formula (k=60 is the standard constant from the original paper)
score(chunk) = Σ  1 / (k + rank_in_list)
```

The constant `k=60` is the value from the Cormack et al. 2009 paper and is the de-facto standard in production RAG systems (used by Cohere, LangChain, LlamaIndex, and Elasticsearch's hybrid search).

**Why not a learned re-ranker?** A cross-encoder re-ranker (e.g. `cross-encoder/ms-marco-MiniLM-L-6-v2`) would give higher precision but requires a second model call per candidate, adding 200–500 ms latency and a GPU-friendly runtime. RRF achieves most of the recall gain at zero additional latency and no extra dependencies.

**Vector index: HNSW (not IVFFlat)**

The original spec called for an IVFFlat index. During Render deployment, IVFFlat was replaced with HNSW for two reasons:

- IVFFlat's k-means build step requires loading all vectors into a contiguous memory region. At 1024 dims (voyage-3), this exceeded Render free-tier PostgreSQL's `maintenance_work_mem` (16 MB), causing a `ProgramLimitExceededError`.
- HNSW builds incrementally (one node at a time), has no batch memory spike, and delivers better recall than IVFFlat at equivalent query latency for datasets under ~1M rows.

HNSW parameters used: `m=16, ef_construction=64` (pgvector defaults; well-established starting point for general RAG workloads).

## Consequences

**Good:**
- Hybrid search consistently outperforms either method alone, especially on exact-match queries that are common in technical documentation RAG.
- Everything runs in the existing PostgreSQL instance — no new services, no new Docker containers, no new dependencies.
- The retrieval logic is ~60 lines of plain SQL + Python (`app/services/retriever.py`), making it easy to explain, test, and extend.
- HNSW works on all PostgreSQL environments including free-tier managed databases with constrained `maintenance_work_mem`.

**Trade-off:**
- Two parallel database queries per request instead of one. In practice the overhead is <5 ms for this dataset size.
- RRF weights both signals equally. For corpora where one signal is clearly dominant, a tuned weighted sum would perform better. RRF is the right default when no training data is available.

**Risk:**
- HNSW uses more memory at query time than IVFFlat (the entire graph is held in shared buffers). For very large datasets (>5M rows) this becomes a concern. At demo scale (~150 rows) it is irrelevant.
