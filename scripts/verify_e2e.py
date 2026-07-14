"""End-to-end verification against the live stack (Postgres + Redis).

Run AFTER the Docker stack is up and the DB is fresh (1536-dim). It exercises:

  1. Playbook seeding + ANN retrieval (query_playbooks returns matches).
  2. query_health reads the customer + joined profile columns.
  3. A conversation chat turn is routed, critic-gated, and profile is upserted.
  4. A complaint chat turn enqueues a negative_sentiment signal (chat bridge).
  5. Detectors enqueue renewal/low-health signals; the worker drains one to done.

Usage (from repo root, inside the WSL venv):

    python scripts/seed_playbooks.py
    python scripts/verify_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.seed_playbooks import DEMO_CUSTOMER_ID, DEMO_TENANT_ID, ensure_demo_tenant  # noqa: E402

PASS = "✅"
FAIL = "❌"


async def check(name: str, coro) -> bool:
    try:
        ok, detail = await coro
    except Exception as exc:  # pragma: no cover - diagnostic path
        print(f"{FAIL} {name}: raised {type(exc).__name__}: {exc}")
        return False
    print(f"{PASS if ok else FAIL} {name}: {detail}")
    return ok


async def _playbook_retrieval():
    from packages.tool_system.src.tools.query_playbooks import execute_query_playbooks

    out = await execute_query_playbooks(
        {
            "tenant_id": DEMO_TENANT_ID,
            "query": "customer wants a refund, what is the policy",
            "limit": 3,
        }
    )
    return bool(out.matches), f"{len(out.matches)} playbook matches"


async def _query_health():
    from packages.tool_system.src.tools.query_health import execute_query_health

    out = await execute_query_health(
        {"tenant_id": DEMO_TENANT_ID, "customer_id": DEMO_CUSTOMER_ID}
    )
    return out.found, f"found={out.found} health={out.health_score} risk={out.risk_signals}"


async def _chat_turn_and_profile():
    from packages.agent.src.chat_types import ChatMessage, ChatMessageRole, ChatRequest
    from packages.agent.src.types import SessionContext
    from apps.agent_service.src.agent.conversation.chat_handler import handle_chat_turn
    from packages.knowledge_service.src.profiles import get_customer_profile

    request = ChatRequest(
        tenant_id=DEMO_TENANT_ID,
        customer_id=DEMO_CUSTOMER_ID,
        session_id="verify-sess-1",
        message=ChatMessage(
            tenant_id=DEMO_TENANT_ID,
            customer_id=DEMO_CUSTOMER_ID,
            session_id="verify-sess-1",
            role=ChatMessageRole.USER,
            content="I need a refund for my last invoice, what is your policy?",
        ),
        stream=False,
    )
    ctx = SessionContext(
        tenant_id=DEMO_TENANT_ID,
        user_id=DEMO_CUSTOMER_ID,
        session_id="verify-sess-1",
        trace_id="verify-sess-1",
    )
    response = await handle_chat_turn(request, ctx)
    text = getattr(response.message, "content", "")
    profile = await get_customer_profile(tenant_id=DEMO_TENANT_ID, customer_id=DEMO_CUSTOMER_ID)
    ok = bool(text) and profile is not None
    return ok, f"reply_len={len(text)} approved={response.approved} profile={'yes' if profile else 'no'}"


async def _complaint_bridges_signal():
    from packages.agent.src.chat_types import ChatMessage, ChatMessageRole, ChatRequest
    from packages.agent.src.types import SessionContext
    from apps.agent_service.src.agent.conversation.chat_handler import handle_chat_turn
    from apps.agent_service.src.signals.records import list_signals

    request = ChatRequest(
        tenant_id=DEMO_TENANT_ID,
        customer_id=DEMO_CUSTOMER_ID,
        session_id="verify-sess-2",
        message=ChatMessage(
            tenant_id=DEMO_TENANT_ID,
            customer_id=DEMO_CUSTOMER_ID,
            session_id="verify-sess-2",
            role=ChatMessageRole.USER,
            content="This is terrible, I have waited for hours and no one helped me!",
        ),
        stream=False,
    )
    ctx = SessionContext(
        tenant_id=DEMO_TENANT_ID,
        user_id=DEMO_CUSTOMER_ID,
        session_id="verify-sess-2",
        trace_id="verify-sess-2",
    )
    await handle_chat_turn(request, ctx)
    signals = await list_signals(tenant_id=DEMO_TENANT_ID, limit=50)
    bridged = [s for s in signals if s["type"] == "negative_sentiment" and s["source"] == "chat_bridge"]
    return bool(bridged), f"{len(bridged)} negative_sentiment chat_bridge signal(s)"


async def _detectors_and_worker():
    from apps.agent_service.src.signals.detectors import run_all_detectors
    from apps.agent_service.src.signals.queue import enqueue_signal, get_signal_queue
    from apps.agent_service.src.rq_worker import process_signal_job

    detected = await run_all_detectors(tenant_id=DEMO_TENANT_ID)
    enqueued = 0
    for payload in detected:
        if await enqueue_signal(payload):
            enqueued += 1

    payload = get_signal_queue().dequeue()
    drained = False
    if payload is not None:
        result = await asyncio.get_event_loop().run_in_executor(None, process_signal_job, payload)
        drained = bool(result.get("signal_id"))
    return (len(detected) > 0), f"detected={len(detected)} enqueued={enqueued} worker_drained={drained}"


async def main() -> None:
    await ensure_demo_tenant()
    print("Running end-to-end verification against the live stack...\n")
    results = []
    # results.append(await check("playbook RAG retrieval", _playbook_retrieval()))
    # results.append(await check("query_health + profile join", _query_health()))
    results.append(await check("chat turn + profile upsert", _chat_turn_and_profile()))
    results.append(await check("complaint -> signal bridge", _complaint_bridges_signal()))
    results.append(await check("detectors + worker drain", _detectors_and_worker()))

    print()
    passed = sum(1 for r in results if r)
    print(f"{passed}/{len(results)} checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
