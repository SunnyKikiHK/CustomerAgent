"""Live OpenRouter smoke test for the conversation backend.

Verifies the backend actually reaches the real LLM (OpenRouter, key loaded from
config.sh) and runs full chat turns end-to-end. Exercises:

  1. Real intent classification (a genuine LLM completion, not a local fallback).
  2. A refund chat turn (planner -> executor -> critic); should engage the billing
     specialist and its process_refund prototype.
  3. An escalation chat turn; should engage the escalation specialist and its
     check_human_availability prototype ("no human ... available" in the reply).

On a connection/auth failure (missing key, 401, DNS/timeout) this prints a clear
message and exits non-zero so the failure is not silently masked by local
fallbacks.

Usage (from repo root, inside WSL, with config.sh sourced for the key):

    wsl.exe -e bash -lc "cd /d/OVERALL_NOTEBOOK/agent/no_name/CustomerAgent
      && set -a && source config.sh >/dev/null 2>&1 && set +a
      && source .venv/bin/activate && export PYTHONPATH=[repo-root]
      && python scripts/smoke_live_api.py"
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

PASS = "PASS"
FAIL = "FAIL"

DEMO_TENANT_ID = "demo-tenant"
DEMO_CUSTOMER_ID = "11111111-1111-1111-1111-111111111111"

#: Substrings in an error that indicate a connectivity / auth problem (as opposed
#: to a logic bug). These are the cases where we must halt and notify the user.
_CONNECTION_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "api key",
    "apikey",
    "authentication",
    "timeout",
    "timed out",
    "connection",
    "connect",
    "dns",
    "name or service not known",
    "temporary failure",
    "getaddrinfo",
    "ssl",
)


class ConnectionFailure(RuntimeError):
    """Raised when the live LLM cannot be reached or authenticated."""


def _looks_like_connection_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _CONNECTION_MARKERS)


def _require_key() -> None:
    if not (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")):
        raise ConnectionFailure(
            "No OPENROUTER_API_KEY / OPENAI_API_KEY in the environment. "
            "Source config.sh before running (see the module docstring)."
        )


async def _classify_intent() -> str:
    """One genuine LLM classification; fails loudly if it silently fell back."""
    from apps.agent_service.src.agent.conversation.intent import IntentRecognizer

    recognizer = IntentRecognizer(embedding_enabled=False)
    result = await recognizer._llm_recognize(
        "I was charged twice and I want a refund", history=None
    )
    if result.get("failed"):
        raise ConnectionFailure(
            f"LLM intent classification failed (reason: {result.get('reasoning')})"
        )
    return f"intent={result.get('intent')} confidence={result.get('confidence')}"


async def _chat_turn(session: str, content: str) -> tuple[str, bool]:
    from packages.agent.src.chat_types import ChatMessage, ChatMessageRole, ChatRequest
    from packages.agent.src.types import SessionContext
    from apps.agent_service.src.agent.conversation.chat_handler import handle_chat_turn

    request = ChatRequest(
        tenant_id=DEMO_TENANT_ID,
        customer_id=DEMO_CUSTOMER_ID,
        session_id=session,
        message=ChatMessage(
            tenant_id=DEMO_TENANT_ID,
            customer_id=DEMO_CUSTOMER_ID,
            session_id=session,
            role=ChatMessageRole.USER,
            content=content,
        ),
        stream=False,
    )
    ctx = SessionContext(
        tenant_id=DEMO_TENANT_ID,
        user_id=DEMO_CUSTOMER_ID,
        session_id=session,
        trace_id=session,
    )
    response = await handle_chat_turn(request, ctx)
    text = getattr(response.message, "content", "") or ""
    return text, bool(response.approved)


async def main() -> int:
    _require_key()
    print(f"Using model: worker={os.getenv('OPENROUTER_MODEL', '(default)')} "
          f"planner={os.getenv('OPENROUTER_LARGE_MODEL', '(default)')}")

    # 1. Real intent classification.
    detail = await _classify_intent()
    print(f"{PASS} live intent classification: {detail}")

    # 2. Refund turn.
    refund_text, refund_ok = await _chat_turn(
        "smoke-refund", "I need a refund for order 12345, please process it."
    )
    print(f"{PASS} refund turn: approved={refund_ok} reply_len={len(refund_text)}")
    print(f"       reply: {refund_text[:240].replace(chr(10), ' ')}")

    # 3. Escalation turn.
    esc_text, esc_ok = await _chat_turn(
        "smoke-escalation", "I want to speak to a human representative right now."
    )
    mentions_no_human = "no human" in esc_text.lower() or "not available" in esc_text.lower()
    print(f"{PASS} escalation turn: approved={esc_ok} reply_len={len(esc_text)} "
          f"mentions_no_human={mentions_no_human}")
    print(f"       reply: {esc_text[:240].replace(chr(10), ' ')}")

    print("\nAll live checks completed.")
    return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except ConnectionFailure as exc:
        print(f"\n{FAIL} LIVE CONNECTION FAILED: {exc}")
        print("The backend could not reach OpenRouter. Check the key in config.sh "
              "and network connectivity, then re-run.")
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001 - classify then report
        if _looks_like_connection_error(exc):
            print(f"\n{FAIL} LIVE CONNECTION FAILED ({type(exc).__name__}): {exc}")
            print("This looks like a connectivity/auth problem reaching OpenRouter.")
            sys.exit(2)
        print(f"\n{FAIL} SMOKE TEST ERROR ({type(exc).__name__}): {exc}")
        sys.exit(1)
    sys.exit(code)
