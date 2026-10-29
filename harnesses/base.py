"""Small public boundary between automated agents and the game runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from .actions import ActionContract, ActionPlan


ObservationMode = Literal["state", "screenshot"]


@dataclass(frozen=True, slots=True)
class HarnessContext:
    game_id: str
    game: Any
    task: Any | None
    action_contract: ActionContract
    # The episode's random seed (game instance), for harnesses that seed sampling with it.
    seed: int | None = None


@dataclass(frozen=True, slots=True)
class HarnessObservation:
    step_index: int
    t: float
    policy_state: dict[str, Any] | None = None
    screenshot_path: Path | None = None
    previous_result: dict[str, Any] | None = None


@dataclass(slots=True)
class Decision:
    plan: ActionPlan = field(default_factory=ActionPlan.empty)
    thinking: str | None = None
    note: str | None = None
    model_output: str | None = None
    # Why the model stopped generating (e.g. "length" when the output budget ran out).
    finish_reason: str | None = None
    # The agent's statement that the game itself is broken; ends the episode.
    report: str | None = None
    # The provider's token usage for this decision, as it reported it.
    usage: dict[str, Any] | None = None
    stop: bool = False
    counts_as_step: bool = True


class AgentHarness(Protocol):
    """A stateful agent that turns observations into canonical action plans."""

    observation_mode: ObservationMode
    step_interval_s: float
    actor_metadata: dict[str, Any]
    action_contract: ActionContract
    page_init_scripts: tuple[str, ...]

    async def reset(self, context: HarnessContext) -> None: ...

    async def step(self, observation: HarnessObservation) -> Decision: ...

    async def close(self) -> None: ...


__all__ = [
    "AgentHarness",
    "Decision",
    "HarnessContext",
    "HarnessObservation",
    "ObservationMode",
]
