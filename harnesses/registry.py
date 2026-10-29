"""Construct the scripted-policy or VLM player."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import AgentHarness
from .vlm import VLMConfig, VLMHarness
from .policy import ScriptedPolicyHarness


def create_harness(
    kind: str,
    *,
    game_id: str,
    model_id: str | None = None,
    policy_spec: str | None = None,
    actor_config: Mapping[str, Any] | None = None,
) -> AgentHarness:
    if kind == "vlm":
        if not model_id:
            raise ValueError(f"{kind} sessions require --model.")
        values = dict(actor_config or {})
        try:
            config = VLMConfig(**values)
        except TypeError as exc:
            raise ValueError(f"Invalid {kind} actor configuration: {exc}") from exc
        return VLMHarness(model_id=model_id, config=config)
    if kind == "policy":
        return ScriptedPolicyHarness(game_id=game_id, policy_spec=policy_spec)
    supported = "policy, vlm"
    raise ValueError(f"Unsupported automated actor: {kind}. Supported: {supported}")


__all__ = ["create_harness"]
