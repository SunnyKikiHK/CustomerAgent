"""QBR: quarterly business review reports.

Stores one generated QBR per tenant/period, including the deterministic metrics
snapshot used to produce it (so a report is reproducible even when customer data
later changes) and the generated narrative markdown. Tenant-scoped with RLS.

Revision ID: 0004_qbr
Revises: 0003_nps
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op

revision = "0004_qbr"
down_revision = "0003_nps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qbr_reports (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            period_start      DATE         NOT NULL,
            period_end        DATE         NOT NULL,
            status            VARCHAR(20)  NOT NULL DEFAULT 'draft',  -- draft, approved, delivered, failed
            metrics_snapshot  JSONB        NOT NULL DEFAULT '{}'::jsonb,
            report_markdown   TEXT,
            generated_at      TIMESTAMPTZ,
            delivered_at      TIMESTAMPTZ,
            recipient_email   VARCHAR(320),
            workflow_id       VARCHAR(255),
            error             TEXT,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_qbr_reports_tenant ON qbr_reports(tenant_id, created_at DESC);"
    )
    op.execute("ALTER TABLE qbr_reports ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS qbr_reports_isolation ON qbr_reports;")
    op.execute(
        """
        CREATE POLICY qbr_reports_isolation ON qbr_reports
            USING (tenant_id::text = current_setting('app.current_tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qbr_reports CASCADE;")
