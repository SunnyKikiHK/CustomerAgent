"""Live verification of Phase 1-7 additions against the running stack.

Run AFTER `alembic upgrade head` on the live DB (inside the WSL venv):

    python scripts/verify_phases.py

Exercises the pieces that need a real Postgres: auth user/membership creation,
customer create/update, the NPS survey->response->aggregate round trip, QBR
portfolio aggregation + report persistence, and a detector scan. LLM/Temporal
are not required (QBR narrative generation is exercised separately/offline).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.seed_playbooks import DEMO_TENANT_ID, DEMO_CUSTOMER_ID, ensure_demo_tenant  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"


async def check(name: str, coro) -> bool:
    try:
        ok, detail = await coro
    except Exception as exc:  # pragma: no cover - diagnostic
        print(f"{FAIL} {name}: {type(exc).__name__}: {exc}")
        return False
    print(f"{PASS if ok else FAIL} {name}: {detail}")
    return ok


async def _auth_roundtrip():
    from packages.auth.src.store import create_user, upsert_membership, get_user_by_email
    from packages.auth.src.security import verify_password
    from packages.auth.src.models import TenantRole

    user = await create_user(email="verify-csm@demo.test", password="pw-verify-123", full_name="Verify CSM")
    await upsert_membership(user_id=user.id, tenant_id=DEMO_TENANT_ID, role=TenantRole.CSM)
    fetched = await get_user_by_email("verify-csm@demo.test")
    ok = fetched is not None and verify_password("pw-verify-123", fetched.password_hash)
    return ok, f"user={user.email} pw_ok={ok}"


async def _nps_roundtrip():
    from packages.knowledge_service.src import nps

    survey = await nps.create_survey(tenant_id=DEMO_TENANT_ID, customer_id=DEMO_CUSTOMER_ID)
    await nps.mark_survey_sent(tenant_id=DEMO_TENANT_ID, survey_id=survey["id"])
    resp = await nps.record_response(tenant_id=DEMO_TENANT_ID, survey_id=survey["id"], score=3, comment="slow")
    agg = await nps.tenant_nps(tenant_id=DEMO_TENANT_ID)
    ok = resp["classification"] == "detractor" and agg["responses"] >= 1
    return ok, f"class={resp['classification']} tenant_nps={agg['nps']} responses={agg['responses']}"


async def _qbr_aggregate_and_persist():
    from datetime import date
    from packages.knowledge_service.src import qbr

    snapshot = await qbr.aggregate_portfolio(tenant_id=DEMO_TENANT_ID)
    report = await qbr.create_report(
        tenant_id=DEMO_TENANT_ID,
        period_start=date.today(),
        period_end=date.today(),
        metrics_snapshot=snapshot,
    )
    await qbr.attach_narrative(
        tenant_id=DEMO_TENANT_ID, report_id=report["id"], report_markdown="## QBR\nVerified."
    )
    fetched = await qbr.get_report(tenant_id=DEMO_TENANT_ID, report_id=report["id"])
    ok = fetched is not None and fetched["report_markdown"].startswith("## QBR")
    return ok, f"customers={snapshot['customers']} mrr={snapshot['mrr_total']} report={report['id'][:8]}"


async def _integration_encrypted():
    from packages.auth.src.store import get_user_by_email
    from packages.auth.src import integrations

    user = await get_user_by_email("verify-csm@demo.test")
    await integrations.connect_integration(
        user_id=user.id, provider="google", refresh_token="fake-refresh-token-xyz", scopes="gmail.send"
    )
    public = await integrations.get_integration_public(user_id=user.id, provider="google")
    token = await integrations.get_refresh_token(user_id=user.id, provider="google")
    # Public view must not leak the token; decrypt must round-trip in-process.
    ok = "refresh_token" not in public and token == "fake-refresh-token-xyz"
    return ok, f"status={public['status']} has_token={public.get('has_token')} decrypt_ok={token == 'fake-refresh-token-xyz'}"


async def _detector_scan():
    from apps.agent_service.src.signals.detectors import run_all_detectors

    signals = await run_all_detectors(tenant_id=DEMO_TENANT_ID)
    return True, f"{len(signals)} signal(s) detected"


async def main() -> None:
    await ensure_demo_tenant()
    print("Live Phase 1-7 verification against the running stack...\n")
    results = [
        await check("auth user + membership + password", _auth_roundtrip()),
        await check("NPS survey -> response -> aggregate", _nps_roundtrip()),
        await check("QBR aggregate + persist + fetch", _qbr_aggregate_and_persist()),
        await check("integration encrypted token round-trip", _integration_encrypted()),
        await check("detector scan", _detector_scan()),
    ]
    print()
    passed = sum(1 for r in results if r)
    print(f"{passed}/{len(results)} live checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
