"""Agent-service runtime package.

During the current repository layout migration, shared agent primitives live in
``packages/agent/src`` while service runtime modules live here. Extending the
package search path lets imports such as ``agent.types`` resolve to the shared
modules without duplicating model definitions.
"""

from __future__ import annotations

from pathlib import Path

_SHARED_AGENT_SRC = Path(__file__).resolve().parents[4] / "packages" / "agent" / "src"
if _SHARED_AGENT_SRC.exists():
    __path__.append(str(_SHARED_AGENT_SRC))

__all__: list[str] = []
