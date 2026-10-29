"""Chronological VLM history, with independently bounded text and screenshots."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from agents.types import ContextEntry
from harnesses.actions import Action


DEFAULT_HISTORY_STEPS = 50
DEFAULT_HISTORY_IMAGES = 6


def _action_text(actions: list[Action]) -> str:
    return json.dumps(actions[0], ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class _Step:
    screenshot: str
    actions: list[Action]
    note: str | None
    feedback: str | None = None


class VLMHistory:
    """Recent actions and notes, with screenshots on the last `images_max` steps."""

    def __init__(self, *, history_steps: int = DEFAULT_HISTORY_STEPS,
                 images_max: int = DEFAULT_HISTORY_IMAGES):
        if images_max < 0:
            raise ValueError("images_max must be non-negative")
        if history_steps < 0:
            raise ValueError("history_steps must be non-negative")
        self.images_max = images_max
        self.history_steps = history_steps
        self._steps: list[_Step] = []

    def clear(self) -> None:
        self._steps.clear()

    def record(self, *, screenshot: Path, actions: list[Action], note: str | None = None) -> None:
        self._steps.append(_Step(str(screenshot), list(actions), note))

    def record_feedback(self, text: str) -> None:
        if self._steps:
            self._steps[-1] = replace(self._steps[-1], feedback=text)

    def entries(self) -> list[ContextEntry]:
        entries: list[ContextEntry] = []
        first_image = len(self._steps) - self.images_max
        first_step = max(0, len(self._steps) - self.history_steps)
        for index in range(first_step, len(self._steps)):
            step = self._steps[index]
            entries.append(ContextEntry(type="text", role="user", text=str(index + 1), field="step"))
            if index >= first_image:
                entries.append(ContextEntry(type="image", role="user", image_path=step.screenshot,
                                            field="screenshot"))
            if step.note:
                entries.append(ContextEntry(type="text", role="assistant", text=step.note, field="note"))
            entries.append(ContextEntry(type="text", role="assistant",
                                        text=_action_text(step.actions) if step.actions else "none (no valid action)",
                                        field="action"))
            if step.feedback:
                entries.append(ContextEntry(type="text", role="user", text=step.feedback, field="feedback"))
        return entries


__all__ = ["DEFAULT_HISTORY_STEPS", "DEFAULT_HISTORY_IMAGES", "VLMHistory"]
