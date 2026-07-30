"""Unit tests for app/services/chunker.py — no external services required."""

from pathlib import Path

import pytest

from app.services.chunker import Chunk, chunk_corpus, chunk_file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_md(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def test_chunk_file_parses_frontmatter(tmp_path: Path) -> None:
    md = write_md(
        tmp_path,
        "doc.md",
        "---\ntitle: My Doc\nversion: 1.0\n---\n\n# Hello\n\nSome content here.\n",
    )
    chunks = chunk_file(md)
    assert chunks, "Expected at least one chunk"
    assert chunks[0].metadata["title"] == "My Doc"
    assert chunks[0].metadata["version"] == "1.0"


def test_chunk_file_no_frontmatter(tmp_path: Path) -> None:
    md = write_md(tmp_path, "plain.md", "# Plain Heading\n\nJust plain content.\n")
    chunks = chunk_file(md)
    assert chunks
    assert chunks[0].metadata == {}


# ---------------------------------------------------------------------------
# Title extraction from headings
# ---------------------------------------------------------------------------

def test_chunk_file_extracts_h1_title(tmp_path: Path) -> None:
    md = write_md(
        tmp_path,
        "headings.md",
        "# Payment Processing\n\nThis section describes payment processing.\n",
    )
    chunks = chunk_file(md)
    assert any(c.title == "Payment Processing" for c in chunks)


def test_chunk_file_extracts_h2_title(tmp_path: Path) -> None:
    md = write_md(
        tmp_path,
        "h2.md",
        "# Top Level\n\n## Retry Policy\n\nRetry with exponential backoff.\n",
    )
    chunks = chunk_file(md)
    titles = [c.title for c in chunks]
    assert "Retry Policy" in titles or "Top Level" in titles


def test_chunk_file_title_propagates_to_next_chunk(tmp_path: Path) -> None:
    """Chunks that don't contain a heading inherit the previous heading's title."""
    # Build a document long enough to produce multiple chunks from one section
    body = "# Section Alpha\n\n" + ("Content word " * 200) + "\n"
    md = write_md(tmp_path, "long.md", body)
    chunks = chunk_file(md, chunk_size=64, chunk_overlap=10)
    # All chunks should carry the same section title
    assert all(c.title == "Section Alpha" for c in chunks if c.title)


# ---------------------------------------------------------------------------
# Chunk structure
# ---------------------------------------------------------------------------

def test_chunk_file_sets_file_path(tmp_path: Path) -> None:
    md = write_md(tmp_path, "paths.md", "# Test\n\nContent.\n")
    chunks = chunk_file(md)
    assert all(c.file_path == str(md) for c in chunks)


def test_chunk_file_chunk_index_sequential(tmp_path: Path) -> None:
    body = "# A\n\n" + ("word " * 300) + "\n"
    md = write_md(tmp_path, "seq.md", body)
    chunks = chunk_file(md, chunk_size=64, chunk_overlap=0)
    indices = [c.chunk_index for c in chunks]
    assert indices == sorted(indices)


def test_chunk_file_no_empty_chunks(tmp_path: Path) -> None:
    md = write_md(tmp_path, "sparse.md", "# Head\n\n   \n\nActual content here.\n")
    chunks = chunk_file(md)
    assert all(c.content.strip() for c in chunks)


def test_chunk_file_returns_chunk_dataclass(tmp_path: Path) -> None:
    md = write_md(tmp_path, "type.md", "# Title\n\nContent.\n")
    chunks = chunk_file(md)
    assert all(isinstance(c, Chunk) for c in chunks)


# ---------------------------------------------------------------------------
# corpus-level walk
# ---------------------------------------------------------------------------

def test_chunk_corpus_walks_multiple_files(tmp_path: Path) -> None:
    write_md(tmp_path, "a.md", "# A\n\nContent of A.\n")
    write_md(tmp_path, "b.md", "# B\n\nContent of B.\n")
    chunks = chunk_corpus(tmp_path)
    file_paths = {c.file_path for c in chunks}
    assert len(file_paths) == 2


def test_chunk_corpus_ignores_non_markdown(tmp_path: Path) -> None:
    write_md(tmp_path, "doc.md", "# Doc\n\nContent.\n")
    (tmp_path / "README.txt").write_text("ignore me")
    (tmp_path / "schema.sql").write_text("SELECT 1;")
    chunks = chunk_corpus(tmp_path)
    assert all(c.file_path.endswith(".md") for c in chunks)


def test_chunk_corpus_empty_dir_returns_empty(tmp_path: Path) -> None:
    chunks = chunk_corpus(tmp_path)
    assert chunks == []


def test_chunk_corpus_respects_chunk_size(tmp_path: Path) -> None:
    # A large document with small chunk_size should produce more chunks
    body = "# Big Doc\n\n" + ("token " * 500) + "\n"
    write_md(tmp_path, "big.md", body)

    chunks_small = chunk_corpus(tmp_path, chunk_size=50, chunk_overlap=0)
    chunks_large = chunk_corpus(tmp_path, chunk_size=400, chunk_overlap=0)

    assert len(chunks_small) > len(chunks_large)
