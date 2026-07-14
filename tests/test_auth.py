"""Offline tests for the auth layer: password hashing, JWT, and RBAC logic.

These exercise the pure/deterministic parts of ``packages.auth`` without a
database or network. DB-backed store functions are covered by live tests.
"""

from __future__ import annotations

import pytest

from packages.auth.src.models import (
    AuthContext,
    Membership,
    TenantRole,
    User,
    UserPublic,
    READ_ROLES,
    WRITE_ROLES,
)
from packages.auth.src.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


# ── Password hashing ──────────────────────────────────────────────────────────

def test_password_hash_round_trip():
    hashed = hash_password("s3cret-pw")
    assert hashed != "s3cret-pw"
    assert verify_password("s3cret-pw", hashed) is True
    assert verify_password("wrong-pw", hashed) is False


def test_password_hash_is_salted():
    """Two hashes of the same password differ (random salt)."""
    assert hash_password("same") != hash_password("same")


def test_verify_password_rejects_garbage_hash():
    assert verify_password("anything", "not-a-valid-hash") is False


# ── JWT ─────────────────────────────────────────────────────────────────────

def test_jwt_round_trip_carries_claims():
    token = create_access_token(
        subject="user-1",
        email="csm@example.com",
        extra_claims={"is_platform_admin": True},
    )
    claims = decode_access_token(token)
    assert claims["sub"] == "user-1"
    assert claims["email"] == "csm@example.com"
    assert claims["is_platform_admin"] is True
    assert "exp" in claims


def test_decode_rejects_tampered_token():
    token = create_access_token(subject="u", email="e@x.com")
    with pytest.raises(ValueError):
        decode_access_token(token + "tampered")


def test_expired_token_is_rejected():
    token = create_access_token(subject="u", email="e@x.com", ttl_seconds=-1)
    with pytest.raises(ValueError):
        decode_access_token(token)


# ── RBAC / AuthContext ────────────────────────────────────────────────────────

def _user(is_admin: bool = False) -> User:
    return User(id="u1", email="e@x.com", is_platform_admin=is_admin)


def test_write_roles_subset_of_read_roles():
    assert WRITE_ROLES <= READ_ROLES
    assert TenantRole.VIEWER in READ_ROLES
    assert TenantRole.VIEWER not in WRITE_ROLES


@pytest.mark.parametrize(
    "role,can_write",
    [
        (TenantRole.PLATFORM_ADMIN, True),
        (TenantRole.TENANT_ADMIN, True),
        (TenantRole.CSM, True),
        (TenantRole.VIEWER, False),
    ],
)
def test_auth_context_can_write_by_role(role, can_write):
    ctx = AuthContext(user=_user(), tenant_id="t1", role=role)
    assert ctx.can_write() is can_write


def test_user_public_omits_password_hash():
    user = User(id="u1", email="e@x.com", password_hash="secret-hash")
    public = UserPublic.from_user(user)
    assert not hasattr(public, "password_hash")
    dumped = public.model_dump()
    assert "password_hash" not in dumped
    assert dumped["email"] == "e@x.com"


def test_membership_defaults():
    m = Membership(tenant_id="t1", user_id="u1")
    assert m.role is TenantRole.CSM
    assert m.receives_qbr is True
    assert m.notification_preferences == {}
