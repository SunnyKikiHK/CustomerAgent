"""Live HTTP smoke of the containerized API gateway.

Exercises auth, customers CRUD, NPS, QBR, signals scan, integrations, skills,
and chat turn against the running Docker stack (default http://localhost:8000).
Exits non-zero on any failed check.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx

from scripts.seed_playbooks import DEMO_CUSTOMER_ID, DEMO_TENANT_ID, ensure_demo_tenant
from scripts.seed_users import DEMO_CSM_EMAIL, DEMO_CSM_PASSWORD

BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
PASS = "PASS"
FAIL = "FAIL"


def _check(name: str, ok: bool, detail: str) -> bool:
    print(f"{PASS if ok else FAIL} {name}: {detail}")
    return ok


async def main() -> None:
    await ensure_demo_tenant()
    # Ensure the demo CSM exists and is a member of the demo tenant.
    from packages.auth.src.models import TenantRole
    from packages.auth.src.store import create_user, get_user_by_email, upsert_membership

    user = await get_user_by_email(DEMO_CSM_EMAIL)
    if user is None:
        user = await create_user(
            email=DEMO_CSM_EMAIL,
            password=DEMO_CSM_PASSWORD,
            full_name="Demo CSM",
        )
    await upsert_membership(user_id=user.id, tenant_id=DEMO_TENANT_ID, role=TenantRole.CSM)

    results: list[bool] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=90.0) as client:
        r = await client.get("/health")
        results.append(
            _check("GET /health", r.status_code == 200 and r.json().get("status") == "ok", r.text[:120])
        )

        r = await client.post(
            "/auth/login",
            json={"email": DEMO_CSM_EMAIL, "password": DEMO_CSM_PASSWORD},
        )
        login_ok = r.status_code == 200 and "access_token" in r.json()
        results.append(_check("POST /auth/login", login_ok, f"{r.status_code} {r.text[:200]}"))
        if not login_ok:
            print("\nAborting remaining checks: login failed.")
            sys.exit(1)
        token = r.json()["access_token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": DEMO_TENANT_ID,
            "Content-Type": "application/json",
        }

        r = await client.get("/auth/me", headers=headers)
        results.append(
            _check(
                "GET /auth/me",
                r.status_code == 200 and r.json().get("email") == DEMO_CSM_EMAIL,
                f"{r.status_code} {r.text[:200]}",
            )
        )

        r = await client.get("/customers", headers=headers)
        results.append(_check("GET /customers", r.status_code == 200 and "customers" in r.json(), f"{r.status_code} {r.text[:200]}"))

        r = await client.post(
            "/customers",
            headers=headers,
            json={
                "name": f"Smoke Co {uuid.uuid4().hex[:8]}",
                "email": f"smoke-{uuid.uuid4().hex[:6]}@example.com",
                "health_score": 42.0,
                "mrr": 250.0,
                "renewal_date": (date.today() + timedelta(days=25)).isoformat(),
                "usage_trend": {
                    "previous_active_users": 100,
                    "current_active_users": 40,
                },
            },
        )
        create_ok = r.status_code in (200, 201) and "id" in r.json()
        customer_id = r.json().get("id") if create_ok else None
        results.append(_check("POST /customers", create_ok, f"{r.status_code} {r.text[:240]}"))

        if create_ok and customer_id:
            r = await client.patch(
                f"/customers/{customer_id}",
                headers=headers,
                json={"health_score": 38.0},
            )
            results.append(
                _check(
                    "PATCH /customers/{id}",
                    r.status_code == 200 and float(r.json().get("health_score", 0)) == 38.0,
                    f"{r.status_code} {r.text[:200]}",
                )
            )

            r = await client.post(
                f"/customers/{customer_id}/usage-events",
                headers=headers,
                json={
                    "usage_trend": {
                        "previous_active_users": 100,
                        "current_active_users": 30,
                    }
                },
            )
            results.append(
                _check(
                    "POST /customers/{id}/usage-events",
                    r.status_code in (200, 201),
                    f"{r.status_code} {r.text[:200]}",
                )
            )

            r = await client.delete(f"/customers/{customer_id}", headers=headers)
            results.append(
                _check(
                    "DELETE /customers/{id}",
                    r.status_code == 200 and r.json().get("deleted") is True,
                    f"{r.status_code} {r.text[:160]}",
                )
            )

        r = await client.get("/nps/tenant", headers=headers)
        results.append(_check("GET /nps/tenant", r.status_code == 200, f"{r.status_code} {r.text[:200]}"))

        r = await client.get("/qbr/reports", headers=headers)
        results.append(_check("GET /qbr/reports", r.status_code == 200 and "reports" in r.json(), f"{r.status_code} {r.text[:200]}"))

        r = await client.post("/qbr/generate", headers=headers)
        results.append(
            _check(
                "POST /qbr/generate",
                r.status_code in (200, 201, 202) and r.json().get("mode") in {"temporal", "inprocess"},
                f"{r.status_code} {r.text[:280]}",
            )
        )

        r = await client.get("/signals", params={"tenant_id": DEMO_TENANT_ID}, headers=headers)
        results.append(_check("GET /signals", r.status_code == 200 and "signals" in r.json(), f"{r.status_code} {r.text[:200]}"))

        r = await client.post(
            "/signals/scan",
            headers=headers,
            json={"tenant_id": DEMO_TENANT_ID},
        )
        results.append(
            _check(
                "POST /signals/scan",
                r.status_code in (200, 201, 202),
                f"{r.status_code} {r.text[:280]}",
            )
        )

        r = await client.get("/integrations/status", headers=headers)
        results.append(
            _check("GET /integrations/status", r.status_code == 200, f"{r.status_code} {r.text[:220]}")
        )

        r = await client.get("/skills", params={"tenant_id": DEMO_TENANT_ID})
        results.append(_check("GET /skills", r.status_code == 200, f"{r.status_code} {r.text[:220]}"))

        # Non-streaming chat turn so we get a JSON body back.
        r = await client.post(
            "/chat/turn",
            headers=headers,
            json={
                "tenant_id": DEMO_TENANT_ID,
                "customer_id": DEMO_CUSTOMER_ID,
                "session_id": f"smoke-{uuid.uuid4().hex[:10]}",
                "content": "Hello, can you help me check my account health?",
                "stream": False,
            },
        )
        chat_body = {}
        try:
            chat_body = r.json()
        except Exception:
            chat_body = {"raw": r.text[:200]}
        chat_ok = r.status_code == 200
        results.append(
            _check(
                "POST /chat/turn",
                chat_ok,
                f"{r.status_code} keys={list(chat_body)[:8] if isinstance(chat_body, dict) else type(chat_body).__name__} body={str(chat_body)[:240]}",
            )
        )

        # Unauthenticated customers list must be rejected.
        r = await client.get("/customers")
        results.append(_check("GET /customers unauth blocked", r.status_code in (401, 403), f"{r.status_code}"))

    print()
    passed = sum(1 for x in results if x)
    print(f"{passed}/{len(results)} HTTP smoke checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
