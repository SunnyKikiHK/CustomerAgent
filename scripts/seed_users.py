"""Seed a demo Customer Success Manager user and tenant memberships.

Creates one CSM user (email/password below) and assigns them to the demo
tenant(s) so the auth flow, tenant selection, and QBR recipient lookup work end
to end on a fresh database. Idempotent: safe to run repeatedly.

Usage (from the repo root, inside the WSL venv):

    python scripts/seed_users.py                       # seed demo CSM on demo tenant
    python scripts/seed_users.py <tenant_uuid> ...     # also assign extra tenants
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Anchor imports at the repo root regardless of CWD.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from packages.auth.src.models import TenantRole  # noqa: E402
from packages.auth.src.store import create_user, upsert_membership  # noqa: E402

#: Stable demo CSM credentials so the frontend login works out of the box.
DEMO_CSM_EMAIL = "csm@demo.io"
DEMO_CSM_PASSWORD = "demo-csm-password"  # noqa: S105 - documented dev default
DEMO_CSM_NAME = "Demo CSM"

#: Reuse the seed_playbooks demo tenant so both scripts agree.
DEMO_TENANT_ID = "11111111-1111-1111-1111-111111111111"
#: A second demo tenant lets the UI exercise "CSM manages multiple tenants".
DEMO_TENANT_ID_2 = "33333333-3333-3333-3333-333333333333"


async def seed(extra_tenants: list[str]) -> None:
    """Create the demo CSM and assign them to the demo tenants."""
    user = await create_user(
        email=DEMO_CSM_EMAIL,
        password=DEMO_CSM_PASSWORD,
        full_name=DEMO_CSM_NAME,
        is_platform_admin=False,
    )
    print(f"user: {user.email} ({user.id})")

    tenant_ids = [DEMO_TENANT_ID, DEMO_TENANT_ID_2, *extra_tenants]
    for tenant_id in dict.fromkeys(tenant_ids):
        try:
            membership = await upsert_membership(
                user_id=user.id,
                tenant_id=tenant_id,
                role=TenantRole.CSM,
                receives_qbr=True,
            )
            print(f"  membership: {membership.tenant_id} role={membership.role.value}")
        except Exception as exc:  # pragma: no cover - tenant may not exist yet
            print(f"  membership skipped for {tenant_id}: {type(exc).__name__}: {exc}")


def main() -> None:
    extra = sys.argv[1:]
    asyncio.run(seed(extra))


if __name__ == "__main__":
    main()
