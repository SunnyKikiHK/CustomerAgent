"""Circuit breaker primitives reused by the MCP tool layer."""

from __future__ import annotations

import time
from enum import Enum


class CircuitState(str, Enum):
    """Breaker lifecycle states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Three-state circuit breaker for tool and provider calls."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 60.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.state = CircuitState.CLOSED
        self.fail_count = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        """Return whether a new call may proceed."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if self.opened_at is not None and time.monotonic() - self.opened_at >= self.recovery_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        """Reset the breaker after a successful call."""
        self.fail_count = 0
        self.state = CircuitState.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        """Record a failure and open the breaker when the threshold is reached."""
        self.fail_count += 1
        if self.fail_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()


__all__ = ["CircuitBreaker", "CircuitState"]
