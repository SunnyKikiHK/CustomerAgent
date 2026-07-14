"""Document ingestion helpers: parse front matter and chunk markdown.

Used by the playbook seeder to turn tenant markdown files into embeddable
chunks stored in pgvector (``knowledge_chunks``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentChunk:
    """One embeddable chunk of a source document."""

    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    """Parse a minimal ``---`` key: value front matter block (no YAML dep)."""
    text = raw.lstrip()
    if not text.startswith("---"):
        return {}, raw
    lines = text.splitlines()
    meta: dict[str, str] = {}
    end_idx: int | None = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = idx
            break
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip("\"'")
    if end_idx is None:
        return {}, raw
    return meta, "\n".join(lines[end_idx + 1 :])


def chunk_text(text: str, *, max_chars: int = 800, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks on paragraph boundaries.

    Paragraphs are kept together where possible; a paragraph longer than
    ``max_chars`` is hard-split. Overlap preserves context across chunk edges.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            for start in range(0, len(para), max_chars - overlap):
                chunks.append(para[start : start + max_chars].strip())
            continue
        if len(current) + len(para) + 2 > max_chars:
            chunks.append(current.strip())
            current = current[-overlap:] if overlap else ""
        current = f"{current}\n\n{para}".strip() if current else para
    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]


def chunk_markdown_document(
    *,
    doc_id: str,
    raw: str,
    extra_metadata: dict[str, Any] | None = None,
    max_chars: int = 800,
) -> list[DocumentChunk]:
    """Parse front matter + chunk a markdown document into DocumentChunks."""
    meta, body = parse_front_matter(raw)
    metadata: dict[str, Any] = {**meta, **(extra_metadata or {})}
    chunks = chunk_text(body, max_chars=max_chars)
    total = len(chunks)
    return [
        DocumentChunk(
            doc_id=f"{doc_id}::chunk-{index}",
            text=chunk,
            metadata={**metadata, "chunk_index": index, "chunk_total": total},
        )
        for index, chunk in enumerate(chunks)
    ]


__all__ = [
    "DocumentChunk",
    "parse_front_matter",
    "chunk_text",
    "chunk_markdown_document",
]
