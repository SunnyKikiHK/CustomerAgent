"""Core action service used by protocol-facing MCP tools."""

from __future__ import annotations

from typing import Any

from apps.tool_gateway.src.approval import (
    ApprovalVerifier,
    get_approval_verifier,
    mark_action_approval_consumed,
)
from apps.tool_gateway.src.contracts import ActionContext, ActionResult
from apps.tool_gateway.src.idempotency import (
    IdempotencyStore,
    get_idempotency_store,
)
from apps.tool_gateway.src.providers import (
    ActionProvider,
    MockEmailProvider,
    MockHumanEscalationProvider,
    MockSlackProvider,
)
from packages.tool_system.src.tools.escalate_to_human import EscalateToHumanInput
from packages.tool_system.src.tools.send_email import SendEmailInput
from packages.tool_system.src.tools.send_slack import SendSlackInput

#: Action name -> input schema used for provider-independent validation.
_ACTION_SCHEMAS = {
    "send_email": SendEmailInput,
    "send_slack": SendSlackInput,
    "escalate_to_human": EscalateToHumanInput,
}


class ActionService:
    """Authorize, deduplicate, validate, and execute external actions."""

    def __init__(
        self,
        *,
        verifier: ApprovalVerifier | None = None,
        idempotency: IdempotencyStore | None = None,
        providers: dict[str, ActionProvider] | None = None,
    ) -> None:
        self.verifier = verifier or get_approval_verifier()
        self.idempotency = idempotency or get_idempotency_store()
        self.providers = providers or {
            "send_email": MockEmailProvider(),
            "send_slack": MockSlackProvider(),
            "escalate_to_human": MockHumanEscalationProvider(),
        }

    async def execute(
        self,
        action_name: str,
        payload: dict[str, Any],
        context: ActionContext,
    ) -> ActionResult:
        """Execute one action only after all server-side checks pass."""
        provider = self.providers.get(action_name)
        if provider is None:
            return self._failure(context, "unsupported action", status="rejected")

        existing = await self.idempotency.get(context.tenant_id, context.idempotency_key)
        if existing is not None:
            return existing.model_copy(update={"status": "duplicate"})
        if not await self.verifier.verify(action_name, context, payload):
            return self._failure(context, "approval verification failed", status="rejected")

        reservation = await self.idempotency.reserve(context.tenant_id, context.idempotency_key)
        if not reservation.created:
            existing = reservation.existing
            if existing is None:
                return self._failure(
                    context,
                    "duplicate action already exists",
                    status="duplicate",
                )
            # Replay of an already-finalized action: surface it as a duplicate
            # while preserving the original provider message id so callers can
            # correlate the two attempts. Actions still in progress already
            # carry status="duplicate".
            if existing.status == "executed":
                return existing.model_copy(update={"status": "duplicate"})
            return existing

        try:
            validated = _validate_payload(action_name, payload, context.tenant_id)
            provider_id = await provider.execute(context.tenant_id, validated)
        except Exception as exc:
            return self._failure(context, _safe_error(exc))

        result = ActionResult(
            success=True,
            status="executed",
            idempotency_key=context.idempotency_key,
            provider_message_id=provider_id,
        )
        # Persist only successful execution; failed attempts may be safely retried.
        await self.idempotency.finalize(context.tenant_id, context.idempotency_key, result)
        if type(self.verifier).__name__ == "PostgresApprovalVerifier":
            await mark_action_approval_consumed(
                tenant_id=context.tenant_id,
                approval_id=context.approval_id,
                action_name=action_name,
            )
        return result

    @staticmethod
    def _failure(
        context: ActionContext,
        error: str,
        *,
        status: str = "failed",
    ) -> ActionResult:
        return ActionResult(
            success=False,
            status=status,
            idempotency_key=context.idempotency_key,
            error=error,
            retryable=status == "failed",
        )


def _validate_payload(action_name: str, payload: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    """Apply the provider-independent action schema and authoritative tenant."""
    safe_payload = dict(payload)
    safe_payload["tenant_id"] = tenant_id
    model = _ACTION_SCHEMAS.get(action_name)
    if model is None:
        raise ValueError(f"unsupported action: {action_name}")
    return model.model_validate(safe_payload).model_dump(mode="json")


def _safe_error(exc: Exception) -> str:
    """Return an error class without leaking recipient content or credentials."""
    return f"action validation or provider failure ({type(exc).__name__})"


_SERVICE: ActionService | None = None


def get_action_service() -> ActionService:
    """Return the process-wide action service."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ActionService()
    return _SERVICE


__all__ = ["ActionService", "get_action_service"]
