"""pgvector-backed retrieval helpers for knowledge, episodic memory, and profiles."""

from __future__ import annotations

import json
import uuid
from typing import Any

from packages.db.src import execute, fetch_all
from packages.knowledge_service.src.embed import embed_text


async def retrieve_documents(
    *,
    tenant_id: str,
    query: str,
    collection: str = "playbooks",
    limit: int = 5,
    metadata_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Retrieve semantic matches from tenant-scoped knowledge_chunks."""
    vector = await embed_text(query)
    rows = await fetch_all(
        """
        select
            id::text,
            source_doc,
            content,
            metadata,
            1 - (embedding <=> $1::vector) as score
        from knowledge_chunks
        where coalesce(metadata->>'collection', 'playbooks') = $2
          and metadata @> $3::jsonb
        order by embedding <=> $1::vector
        limit $4
        """,
        _pgvector_literal(vector),
        collection,
        metadata_filter or {},
        limit,
        tenant_id=tenant_id,
    )
    return [
        {
            "id": row["id"],
            "source_doc": row["source_doc"],
            "text": row["content"],
            "content": row["content"],
            "metadata": dict(row["metadata"] or {}),
            "score": float(row["score"]) if row["score"] is not None else None,
        }
        for row in rows
    ]


async def store_document(
    *,
    tenant_id: str,
    collection: str,
    doc_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Persist a document and its embedding for later retrieval."""
    payload = dict(metadata or {})
    payload["collection"] = collection
    vector = await embed_text(text)
    stable_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant_id}:{collection}:{doc_id}"))
    status = await execute(
        """
        insert into knowledge_chunks (id, tenant_id, source_doc, content, embedding, metadata)
        values ($1::uuid, $2::uuid, $3, $4, $5::vector, $6::jsonb)
        on conflict (id) do update
        set source_doc = excluded.source_doc,
            content = excluded.content,
            embedding = excluded.embedding,
            metadata = excluded.metadata
        """,
        stable_id,
        tenant_id,
        f"{collection}:{doc_id}",
        text,
        _pgvector_literal(vector),
        payload,
        tenant_id=tenant_id,
    )
    return status.startswith("INSERT") or status.startswith("UPDATE")


def _pgvector_literal(values: list[float]) -> str:
    """Serialize a Python list into pgvector's text input format."""
    return "[" + ",".join(f"{value:.6f}" for value in values) + "]"


__all__ = ["retrieve_documents", "store_document"]
