import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^#{1,2}\s+(.+)$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_tokenizer = tiktoken.get_encoding("cl100k_base")


def _token_len(text: str) -> int:
    return len(_tokenizer.encode(text))


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Strip YAML front-matter and return (metadata_dict, body)."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw

    metadata: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            metadata[key.strip()] = value.strip().strip('"')

    body = raw[match.end():]
    return metadata, body


def _nearest_heading(text: str, current: str | None) -> str | None:
    """Return the last H1/H2 heading found in text, or current if none found."""
    matches = _HEADING_RE.findall(text)
    return matches[-1].strip() if matches else current


@dataclass
class Chunk:
    content: str
    file_path: str
    title: str | None
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


def chunk_file(path: Path, chunk_size: int = 512, chunk_overlap: int = 50) -> list[Chunk]:
    """Split a markdown file into overlapping chunks with metadata."""
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(raw)

    splitter = RecursiveCharacterTextSplitter(
        separators=["\n# ", "\n## ", "\n### ", "\n\n", "\n", " "],
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=_token_len,
    )

    texts = splitter.split_text(body)

    chunks: list[Chunk] = []
    current_title: str | None = frontmatter.get("title")

    for i, text in enumerate(texts):
        current_title = _nearest_heading(text, current_title)
        stripped = text.strip()
        if not stripped:
            continue
        chunks.append(
            Chunk(
                content=stripped,
                file_path=str(path),
                title=current_title,
                chunk_index=i,
                metadata=frontmatter,
            )
        )

    logger.debug("Chunked %s → %d chunks", path.name, len(chunks))
    return chunks


def chunk_corpus(corpus_path: Path, chunk_size: int = 512, chunk_overlap: int = 50) -> list[Chunk]:
    """Recursively chunk all markdown files under corpus_path."""
    md_files = sorted(corpus_path.rglob("*.md"))
    if not md_files:
        logger.warning("No markdown files found under %s", corpus_path)

    all_chunks: list[Chunk] = []
    for md_file in md_files:
        all_chunks.extend(chunk_file(md_file, chunk_size, chunk_overlap))

    logger.info(
        "Corpus chunked: %d files → %d total chunks",
        len(md_files),
        len(all_chunks),
    )
    return all_chunks
