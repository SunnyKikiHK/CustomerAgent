"""pgvector-backed retrieval helpers.

Phase 1 returns an empty result set when the knowledge store is not wired.
The MCP retrieval layer treats this as a successful empty recall and degrades
cleanly while playbooks remain unseeded.
"""

from __future__ import annotations

from typing import Any


async def retrieve_documents(
    *,
    tenant_id: str,
    query: str,
    collection: str = "playbooks",
    limit: int = 5,
    metadata_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Retrieve semantic matches for a tenant-scoped query."""
    _ = (tenant_id, query, collection, limit, metadata_filter)
    return []


async def store_document(
    *,
    tenant_id: str,
    collection: str,
    doc_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Persist a document for later retrieval."""
    _ = (tenant_id, collection, doc_id, text, metadata)
    return False


__all__ = ["retrieve_documents", "store_document"]
