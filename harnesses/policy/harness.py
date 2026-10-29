"""Adapter from existing state policies to the common harness interface."""

from __future__ import annotations

import hashlib
import inspect
from typing import Any

from harnesses.actions import Action, ActionContract, ActionPlan
from harnesses.base import Decision, HarnessContext, HarnessObservation
from .policies import builtin_policy_path, get_policy
from .probe import load_probe


_STICKY_KEYS = {"ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"}


class ScriptedPolicyHarness:
    observation_mode = "state"

    def __init__(self, *, game_id: str, policy_spec: str | None = None):
        self.game_id = game_id
        self.policy_spec = policy_spec
        self.probe = load_probe(game_id)
        self.page_init_scripts = (self.probe.source,)
        self.policy = get_policy(game_id, policy_spec)
        self._configure_policy()
        self._held_key: str | None = None
        self.action_contract = ActionContract(
            max_actions=2 if self._sticky else 1,
            duration_policy="optional",
        )
        self.actor_metadata = {
            "kind": "policy",
            "policy": policy_spec or f"builtin:{game_id}",
            "policy_probe": self.probe.path.name,
            "policy_probe_sha256": self.probe.sha256,
        }
        if policy_spec is None:
            policy_bytes = builtin_policy_path(game_id).read_bytes()
            self.actor_metadata["policy_sha256"] = hashlib.sha256(policy_bytes).hexdigest()

    async def reset(self, context: HarnessContext) -> None:
        self.policy = get_policy(self.game_id, self.policy_spec)
        self._held_key = None
        reset = getattr(self.policy, "reset", None)
        if callable(reset):
            result = reset(context)
            if inspect.isawaitable(result):
                await result
        self._configure_policy()

    async def step(self, observation: HarnessObservation) -> Decision:
        raw = self.policy.act(observation.policy_state)
        if not self._sticky:
            plan = self.action_contract.normalize(raw)
            return Decision(plan=plan, counts_as_step=bool(plan.actions))
        plan = self.action_contract.normalize(self._sticky_actions(raw))
        return Decision(plan=plan, counts_as_step=bool(plan.actions))

    def _configure_policy(self) -> None:
        raw_interval = float(getattr(self.policy, "step_interval_s", 0.5))
        self._sticky = bool(getattr(self.policy, "sticky_keys", False))
        # A zero interval requests the fastest safe loop; None must still yield.
        self.step_interval_s = max(0.008, raw_interval)

    def _sticky_actions(self, raw: object) -> list[Action]:
        desired: str | None = None
        if isinstance(raw, dict) and raw.get("action") == "press_key":
            key = raw.get("key")
            if isinstance(key, str) and key in _STICKY_KEYS:
                desired = key

        if desired is not None:
            if desired == self._held_key:
                return []
            self._held_key = desired
            return [{"action": "set_keys", "keys": [desired]}]

        actions: list[Action] = []
        if self._held_key is not None:
            actions.append({"action": "set_keys", "keys": []})
            self._held_key = None
        if isinstance(raw, dict):
            actions.append(dict(raw))
        return actions

    async def close(self) -> None:
        self._held_key = None


__all__ = ["ScriptedPolicyHarness"]
