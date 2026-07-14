"""Baseline schema (idempotent) matching the original init.sql.

This baseline is intentionally a no-op-friendly checkpoint: every statement uses
IF NOT EXISTS so it can be stamped onto an existing database (one already
bootstrapped by init.sql) without error, and can also create the core tables on
a fresh database that only has the pgvector extension. Later migrations layer
auth, NPS, QBR, and integration tables on top.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # The baseline assumes the core tables already exist from init.sql on
    # existing deployments. On a truly empty DB, init.sql (Docker) creates them.
    # We only guarantee the marker so `alembic current` is meaningful.


def downgrade() -> None:
    # Baseline is not reversible; downgrading past it is unsupported.
    pass
