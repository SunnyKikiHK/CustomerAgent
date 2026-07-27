"""Representative-case latency eval for the GeneralAgent orchestrator.

Runs a representative case mix (ambiguous, compound-billing, adversarial,
tool/technical, policy/refund) through the real conversation pipeline, capturing
per-turn latency, LLM-call count, token usage, ReAct step count, and
forced-synthesis/collapsed flags via the orchestrator's TurnMetrics. Writes
results to eval/new_archi/representative.json (incrementally, after each case,
so a slow/killed run still leaves partial results on disk).

Usage: python scripts/eval_conversation_representative.py [--limit N]
  --limit N   only run the first N of the 5 representative cases (default: all).

Run inside WSL with the LLM key + DB env loaded (see the WSL notes).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CASE_IDS = ["amb-01", "amb-02", "tool-01", "pol-01", "adv-02"]
_TENANT = "demo-tenant"
_CUSTOMER = "11111111-1111-1111-1111-111111111111"


async def _run_case(case) -> dict:
    from packages.agent.src.chat_types import ChatMessage, ChatMessageRole
    from packages.agent.src.orchestration_types import ConversationAgentInput
    from packages.agent.src.types import SessionContext
    from packages.observability.src.collector import collect_spans

    from apps.agent_service.src.agent.conversation.conversation_orchestrator import (
        ConversationOrchestrator,
    )

    session_id = f"repr-{case.id}-{uuid.uuid4().hex[:6]}"
    agent_input = ConversationAgentInput(
        tenant_id=_TENANT,
        customer_id=_CUSTOMER,
        session_id=session_id,
        message=ChatMessage(
            tenant_id=_TENANT,
            customer_id=_CUSTOMER,
            session_id=session_id,
            role=ChatMessageRole.USER,
            content=case.message,
        ),
        stream=False,
    )
    ctx = SessionContext(
        tenant_id=_TENANT, user_id=_CUSTOMER, session_id=session_id, trace_id=session_id
    )
    orch = ConversationOrchestrator()
    # Build the loop directly so we can read its TurnMetrics after the run.
    config = await orch.load_config(ctx)
    constraints = await orch.load_tenant_constraints(ctx)
    memory_excerpt, history = await orch._prepare_turn(agent_input, ctx, config)
    loop = orch._build_loop(agent_input, ctx, config, memory_excerpt, history, constraints)

    started = time.monotonic()
    with collect_spans() as spans:
        try:
            async for _ in loop.run_stream():
                pass
            text = loop.final_text
            error = None
        except Exception as exc:  # a crash is itself a signal
            text = ""
            error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.monotonic() - started) * 1000.0

    return {
        "case_id": case.id,
        "category": getattr(case.category, "value", str(case.category)),
        "message": case.message,
        "latency_ms": round(latency_ms, 1),
        "response_preview": (text or "")[:200],
        "error": error,
        "metrics": loop.metrics.to_dict(),
        "span_names": [s.name for s in spans],
    }


async def _main() -> None:
    from packages.evaluation.src.cases import ALL_CASES

    parser = argparse.ArgumentParser(description="Representative-case conversation latency eval.")
    parser.add_argument("--limit", type=int, default=0, help="Only run the first N cases.")
    args = parser.parse_args()

    by_id = {c.id: c for c in ALL_CASES}
    case_ids = _CASE_IDS[: args.limit] if args.limit > 0 else _CASE_IDS
    cases = [by_id[c] for c in case_ids if c in by_id]
    print(f"Running {len(cases)} representative cases: {[c.id for c in cases]}", flush=True)

    out_path = _ROOT / "eval" / "new_archi" / "representative.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    for case in cases:
        started = time.monotonic()
        print(f"  -> {case.id} ...", flush=True)
        result = await _run_case(case)
        results.append(result)
        print(f"     done in {result['latency_ms']:.0f} ms", flush=True)

        # Write after every case so a slow/killed run still leaves partial data.
        lat = sorted(r["latency_ms"] for r in results)
        out = {
            "cases": len(results),
            "latency_ms": {
                "p50": lat[len(lat) // 2] if lat else 0,
                "p95": lat[max(0, int(0.95 * len(lat)) - 1)] if lat else 0,
                "min": lat[0] if lat else 0,
                "max": lat[-1] if lat else 0,
            },
            "results": results,
        }
        out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    print(json.dumps(out, indent=2, default=str))
    print(f"\nwrote: {out_path}")


if __name__ == "__main__":
    asyncio.run(_main())
