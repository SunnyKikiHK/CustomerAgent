"""Seed tenant playbook markdown into pgvector for RAG retrieval.

Chunks each markdown file under ``skills/<tenant>/playbooks/``, embeds each
chunk, and stores it in ``knowledge_chunks`` (collection="playbooks") so
``query_playbooks`` returns real ANN matches.

Usage (from the repo root, inside the WSL venv):

    python scripts/seed_playbooks.py                 # seed the demo tenant
    python scripts/seed_playbooks.py <tenant_uuid>   # seed a specific tenant

The script ensures a demo tenant + demo customer exist so the FK-backed insert
and later retrieval work end to end on a fresh database.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

# Anchor imports at the repo root regardless of CWD.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from packages.db.src import execute, fetch_one  # noqa: E402
from packages.knowledge_service.src.ingest import chunk_markdown_document  # noqa: E402
from packages.knowledge_service.src.retrieve import store_document  # noqa: E402

#: Stable demo tenant/customer UUIDs so repeated runs are idempotent and the
#: frontend/dashboard has something to show out of the box.
DEMO_TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEMO_CUSTOMER_ID = "22222222-2222-2222-2222-222222222222"


async def ensure_demo_tenant() -> None:
    """Create the demo tenant + a sample customer if they do not exist."""
    await execute(
        """
        insert into tenants (id, name, plan)
        values ($1::uuid, $2, 'growth')
        on conflict (id) do nothing
        """,
        DEMO_TENANT_ID,
        "Demo Tenant",
        tenant_id=DEMO_TENANT_ID,
    )
    await execute(
        """
        insert into customers (id, tenant_id, name, email, health_score, mrr,
                               renewal_date, nps, usage_trend)
        values ($1::uuid, $2::uuid, $3, $4, $5, $6,
                current_date + interval '30 day', $7, $8::jsonb)
        on conflict (id) do nothing
        """,
        DEMO_CUSTOMER_ID,
        DEMO_TENANT_ID,
        "Acme Corp",
        "cto@acme.example",
        42.0,          # low health -> triggers low_health detector
        499.0,
        35,
        '{"weekly_active_users": 8, "trend": "down"}',
        tenant_id=DEMO_TENANT_ID,
    )


async def seed_tenant(tenant_id: str) -> int:
    """Seed all playbook markdown files for a tenant. Returns chunk count."""
    playbook_dir = _ROOT / "skills" / "demo-tenant" / "playbooks"
    if not playbook_dir.exists():
        print(f"No playbook directory at {playbook_dir}")
        return 0

    total_chunks = 0
    for path in sorted(playbook_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        chunks = chunk_markdown_document(
            doc_id=path.stem,
            raw=raw,
            extra_metadata={"source_file": path.name},
        )
        for chunk in chunks:
            title = chunk.metadata.get("title", path.stem)
            signal_type = chunk.metadata.get("signal_type")
            metadata = {
                "title": title,
                "chunk_index": chunk.metadata.get("chunk_index"),
            }
            if signal_type:
                metadata["signal_type"] = signal_type
            ok = await store_document(
                tenant_id=tenant_id,
                collection="playbooks",
                doc_id=chunk.doc_id,
                text=chunk.text,
                metadata=metadata,
            )
            if ok:
                total_chunks += 1
        print(f"  seeded {len(chunks)} chunks from {path.name}")
    return total_chunks


async def main() -> None:
    tenant_id = sys.argv[1] if len(sys.argv) > 1 else DEMO_TENANT_ID
    # Validate UUID early with a clear message.
    uuid.UUID(tenant_id)

    if tenant_id == DEMO_TENANT_ID:
        await ensure_demo_tenant()
        print(f"Ensured demo tenant {DEMO_TENANT_ID} and sample customer.")

    count = await seed_tenant(tenant_id)
    print(f"Seeded {count} playbook chunks for tenant {tenant_id}.")

    # Sanity check: confirm retrieval returns something.
    row = await fetch_one(
        "select count(*)::int as n from knowledge_chunks where metadata->>'collection' = 'playbooks'",
        tenant_id=tenant_id,
    )
    print(f"knowledge_chunks playbook rows for tenant: {row['n'] if row else 0}")


if __name__ == "__main__":
    asyncio.run(main())
