"""Integrations: per-user encrypted OAuth credentials (Google/Gmail).

Stores one integration row per (user, provider). The refresh token is stored
encrypted (Fernet, ``enc:v1:`` prefix); the platform OAuth client id/secret live
in deployment config, not here. Never expose the decrypted token through an API.

Revision ID: 0005_integrations
Revises: 0004_qbr
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op

revision = "0005_integrations"
down_revision = "0004_qbr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_integrations (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider               VARCHAR(40)  NOT NULL DEFAULT 'google',
            account_email          VARCHAR(320),
            refresh_token_enc      TEXT,          -- Fernet ciphertext (enc:v1:...)
            scopes                 TEXT,
            status                 VARCHAR(20)  NOT NULL DEFAULT 'connected',  -- connected, revoked, error
            connected_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
            UNIQUE (user_id, provider)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_integrations_user ON user_integrations(user_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_integrations CASCADE;")
