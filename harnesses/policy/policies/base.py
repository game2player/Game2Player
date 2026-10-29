"""Policy protocol: observe policy-probe state and emit canonical actions.

A policy may also define ``reset(context)`` to select small task-specific
parameters while keeping one shared implementation per game.
"""

from __future__ import annotations

from typing import Any, Protocol

Action = dict[str, Any]


class Policy(Protocol):
    def act(self, policy_state: dict[str, Any] | None) -> Action | None:
        """Return one ActionExecutor action, or None to wait briefly."""
