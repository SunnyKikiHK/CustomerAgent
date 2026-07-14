"""Auth: users and tenant_memberships.

Adds platform-level identity (``users``) and the per-tenant role assignment
(``tenant_memberships``) that backs JWT-derived authorization. A single user
can be a member of multiple tenants with a distinct role in each; one row per
(user, tenant).

Revision ID: 0002_auth
Revises: 0001_baseline
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op

revision = "0002_auth"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email             VARCHAR(320) NOT NULL UNIQUE,
            full_name         VARCHAR(255),
            password_hash     VARCHAR(255) NOT NULL,
            is_platform_admin BOOLEAN      NOT NULL DEFAULT FALSE,
            is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_memberships (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id                  UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role                     VARCHAR(50)  NOT NULL DEFAULT 'csm',
            receives_qbr             BOOLEAN      NOT NULL DEFAULT TRUE,
            notification_preferences JSONB        NOT NULL DEFAULT '{}'::jsonb,
            created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, user_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user ON tenant_memberships(user_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant ON tenant_memberships(tenant_id);"
    )

    # Row-level security: a user sees only memberships for tenants they belong to.
    op.execute("ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY;")
    op.execute(
        """
        DROP POLICY IF EXISTS tenant_memberships_isolation ON tenant_memberships;
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_memberships_isolation ON tenant_memberships
            USING (tenant_id::text = current_setting('app.current_tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_memberships CASCADE;")
    op.execute("DROP TABLE IF EXISTS users CASCADE;")
