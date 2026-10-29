"""VLM player: one model turn produces one action through the shared executor."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agents import create_client, create_client_config
from catalog.models import load_model
from catalog.models._base import thinking_level
from agents.types import ContextEntry
from harnesses.actions import NOTES_MAX_WORDS, REPORT_ACTION, ActionPlan
from harnesses.base import Decision, HarnessContext, HarnessObservation

from .memory import DEFAULT_HISTORY_IMAGES, DEFAULT_HISTORY_STEPS, VLMHistory
from .prompting import ACTION_CONTRACT, build_prompt


@dataclass(frozen=True, slots=True)
class VLMConfig:
    """Model request options and the lengths of its chronological history."""

    history_steps: int = DEFAULT_HISTORY_STEPS
    images_max: int = DEFAULT_HISTORY_IMAGES
    # A thinking level the model profile offers; anything but "off" also switches the
    # prompt to its thinking form.
    thinking: str | None = None
    # Request fields set by the run (temperature, top_p, max_tokens, ...). The value
    # "episode" stands for the episode's random seed.
    sampling: Mapping[str, Any] = field(default_factory=dict)
    # Offer REPORT_ACTION, with which the agent ends an episode whose game is broken.
    report_tool: bool = False
    # Every `review_every` steps the model also rewrites its notes; 0 turns it off.
    review_every: int = 0

    def __post_init__(self) -> None:
        if self.thinking is not None:
            object.__setattr__(self, "thinking", thinking_level(self.thinking))
        object.__setattr__(self, "sampling", dict(self.sampling or {}))
        if self.images_max < 0:
            raise ValueError("images_max must be non-negative")
        if self.history_steps < 0:
            raise ValueError("history_steps must be non-negative")
        if self.review_every < 0:
            raise ValueError("review_every must be non-negative")


class VLMHarness:
    """Screenshot-based player with one configurable chronological history."""

    agent_id = "vlm"
    observation_mode = "screenshot"
    step_interval_s = 0.05
    page_init_scripts: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        model_id: str,
        config: VLMConfig | None = None,
    ):
        self.model_id = model_id
        self.profile = load_model(model_id)
        requested = config or VLMConfig()
        self.config = replace(requested, thinking=self.profile.resolve_thinking(requested.thinking))
        self.action_contract = replace(
            ACTION_CONTRACT,
            report=self.config.report_tool,
            notes=self.config.review_every > 0,
        )
        self._memory = VLMHistory(
            history_steps=self.config.history_steps, images_max=self.config.images_max,
        )
        self._client = None
        self._screenshots: deque[Path] = deque()
        self._steps = 0
        self._notes: tuple[int, str] | None = None  # (step it was written at, text)
        self.actor_metadata = {
            "kind": "vlm",
            "agent": self.agent_id,
            "model_id": model_id,
            "model": self.profile.model,
            "backend": self.profile.backend,
            "codec": self.profile.codec,
            "model_profile": self.profile.to_mapping(),
            "config": {
                "thinking": self.config.thinking,
                "sampling": dict(self.config.sampling),
                "report_tool": self.config.report_tool,
                "images_max": self.config.images_max,
                "history_steps": self.config.history_steps,
                "review_every": self.config.review_every,
            },
            "action_contract": {
                "max_actions": self.action_contract.max_actions,
                "duration_policy": self.action_contract.duration_policy,
                "report": self.action_contract.report,
                "note": self.action_contract.note,
                "notes": self.action_contract.notes,
            },
        }

    async def reset(self, context: HarnessContext) -> None:
        self._clear_state()
        self._client = create_client(
            create_client_config(
                self._request_profile(context.seed),
                system_prompt=build_prompt(
                    context=context,
                    profile=self.profile,
                    contract=self.action_contract,
                    memory_enabled=self.config.history_steps > 0,
                    thinking=self.config.thinking != "off",
                ),
                action_contract=self.action_contract,
            )
        )

    def _request_profile(self, seed: int | None):
        """The profile with the run's sampling and thinking level in its request fields."""
        sampling = {}
        for name, value in self.config.sampling.items():
            if value == "episode":
                if seed is None:
                    raise ValueError(f"sampling.{name} asks for the episode seed, which this run lacks")
                value = seed
            sampling[name] = value
        options = _merged(
            self.profile.request_options,
            sampling,
            self.profile.thinking_request(self.config.thinking),
        )
        return replace(self.profile, request_options=options)

    async def step(self, observation: HarnessObservation) -> Decision:
        if self._client is None:
            raise RuntimeError("vlm.reset() must be called before step().")
        screenshot = observation.screenshot_path
        if screenshot is None:
            raise RuntimeError("vlm requires a screenshot observation.")

        result = getattr(observation, "previous_result", None) or {}
        feedback = "\n".join(
            "Action was not executed: " + json.dumps(error.get("action"), ensure_ascii=False)
            + ". Reason: " + str(error["reason"]).replace("_", " ") + "."
            for error in result.get("errors", [])
        )
        if feedback:
            self._memory.record_feedback(feedback)
        context_entries = self._memory.entries()
        if feedback and not any(entry.field == "feedback" and entry.text == feedback
                                for entry in context_entries):
            context_entries.append(ContextEntry(type="text", role="user", text=feedback, field="feedback"))
        review = self._review_due()
        # After the cached history, so neither moves the request prefix.
        if self._notes is not None:
            written_at, notes = self._notes
            context_entries.append(ContextEntry(
                type="text", role="user", field="notes", text=f"(written at step {written_at}) {notes}"))
        if review:
            context_entries.append(ContextEntry(
                type="text", role="user", field="review",
                text=f"step {self._steps + 1} is a review step: also rewrite your notes in `notes`."))
        try:
            actions = await asyncio.to_thread(
                self._client.get_actions,
                screenshot,
                context_entries,
            )
            trace = self._client.pop_logged_interaction() or {}
            thinking = trace.get("thinking")
            thinking = thinking if isinstance(thinking, str) else None
            finish_reason = trace.get("finish_reason")
            usage = trace.get("usage") if isinstance(trace.get("usage"), Mapping) else None
            arguments = trace.get("tool_arguments") if isinstance(trace.get("tool_arguments"), Mapping) else {}
            if review:
                self._keep_notes(arguments.get("notes"))
            if actions and actions[0].get("action") == REPORT_ACTION:
                return Decision(
                    thinking=thinking,
                    note=arguments.get("note"),
                    model_output=str(trace.get("response_text") or ""),
                    finish_reason=finish_reason,
                    report=str(actions[0].get("reason") or "").strip() or "(no reason given)",
                    usage=usage,
                )
            note = arguments.get("note")
            self._memory.record(
                screenshot=screenshot,
                actions=actions,
                note=note.strip() if isinstance(note, str) and note.strip() else None,
            )
            return Decision(
                plan=ActionPlan(actions),
                thinking=thinking,
                note=arguments.get("note"),
                model_output=str(trace.get("response_text") or ""),
                finish_reason=finish_reason,
                usage=usage,
            )
        finally:
            self._steps += 1
            self._screenshots.append(screenshot)
            self._trim_screenshots()

    def _review_due(self) -> bool:
        every = self.config.review_every
        return every > 0 and self._steps > 0 and self._steps % every == 0

    def _keep_notes(self, notes: object) -> None:
        """Replace the notes with the ones written on this review step; a review that
        wrote none keeps the old ones."""
        if not isinstance(notes, str) or not notes.strip():
            return
        words = notes.split()
        if len(words) > NOTES_MAX_WORDS:
            notes = " ".join(words[:NOTES_MAX_WORDS]) + " ..."
        self._notes = (self._steps + 1, notes.strip())

    async def close(self) -> None:
        self._client = None
        self._clear_state()

    def _trim_screenshots(self) -> None:
        keep = min(self.config.images_max, self.config.history_steps)
        while len(self._screenshots) > keep:
            self._screenshots.popleft().unlink(missing_ok=True)

    def _clear_state(self) -> None:
        self._memory.clear()
        self._steps = 0
        self._notes = None
        while self._screenshots:
            self._screenshots.popleft().unlink(missing_ok=True)


def _merged(*parts: Mapping[str, Any]) -> dict[str, Any]:
    """Later request fields win; nested mappings (e.g. chat_template_kwargs) merge."""
    out: dict[str, Any] = {}
    for part in parts:
        for key, value in part.items():
            if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
                out[key] = _merged(out[key], value)
            else:
                out[key] = dict(value) if isinstance(value, Mapping) else value
    return out


__all__ = ["VLMHarness", "VLMConfig"]
