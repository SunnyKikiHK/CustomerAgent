"""NPS: surveys and responses.

Separates the survey invitation (``nps_surveys``) from the individual response
(``nps_responses``). A response score is a single 0-10 survey answer; the
aggregate NPS (-100..100) is computed deterministically from responses and is
never stored as a customer field. Both tables are tenant-scoped with RLS.

Revision ID: 0003_nps
Revises: 0002_auth
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op

revision = "0003_nps"
down_revision = "0002_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS nps_surveys (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            customer_id  UUID         NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            status       VARCHAR(20)  NOT NULL DEFAULT 'created',  -- created, sent, responded, expired
            sent_at      TIMESTAMPTZ,
            expires_at   TIMESTAMPTZ,
            responded_at TIMESTAMPTZ,
            workflow_id  VARCHAR(255),
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS nps_responses (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            survey_id   UUID         NOT NULL REFERENCES nps_surveys(id) ON DELETE CASCADE,
            customer_id UUID         NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            score       INTEGER      NOT NULL CHECK (score >= 0 AND score <= 10),
            comment     TEXT,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nps_surveys_tenant ON nps_surveys(tenant_id, status);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nps_surveys_customer ON nps_surveys(tenant_id, customer_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nps_responses_tenant ON nps_responses(tenant_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nps_responses_survey ON nps_responses(survey_id);"
    )

    for table in ("nps_surveys", "nps_responses"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
        op.execute(
            f"""
            CREATE POLICY {table}_isolation ON {table}
                USING (tenant_id::text = current_setting('app.current_tenant_id', true));
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS nps_responses CASCADE;")
    op.execute("DROP TABLE IF EXISTS nps_surveys CASCADE;")
