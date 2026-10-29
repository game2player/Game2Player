"""Typed task catalog records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .._yaml import (
    as_bool,
    as_mapping,
    as_optional_float,
    as_optional_text,
    as_text,
)


# Default decision budgets by task difficulty; --max-steps can override them.
DIFFICULTY_MAX_STEPS = {
    "easy": 100,
    "medium": 200,
    "hard": 300,
    "very_hard": 500,
}
DEFAULT_DIFFICULTY = "medium"

# Tasks are numbered within a generated game: <game number>_<NN>.
TASK_ID_PATTERN = re.compile(r"(?P<game>\d+)_(?P<index>\d{2,})\Z")


def expected_task_id_prefix(game_id: str) -> str:
    """The `<game number>_` every task id of this game must start with."""
    return f"{game_id.split('_', 1)[0]}_"


@dataclass(slots=True)
class TaskSpec:
    """Task definition loaded from one task YAML file."""

    task_id: str
    game_id: str
    task_prompt: str = ""
    game_url_suffix: str | None = None
    evaluator_id: str = "noop"
    evaluator_config: dict[str, object] = field(default_factory=dict)
    task_start_score_field: float = 0.0
    task_target_score_field: float | None = None
    pause_during_inference: bool = True
    continue_on_fail: bool = True
    difficulty: str = DEFAULT_DIFFICULTY

    @property
    def max_steps(self) -> int:
        return DIFFICULTY_MAX_STEPS[self.difficulty]

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any] | None,
    ) -> TaskSpec:
        """Parse a task definition from YAML data."""
        raw = as_mapping(data)
        if "task_goal" in raw:
            raise ValueError("Task YAML must use 'task_prompt', not legacy 'task_goal'.")
        if "max_steps" in raw:
            raise ValueError(
                "Task YAML must not set 'max_steps'; it is derived from 'difficulty'."
            )

        task_id = as_optional_text(raw.get("task_id"))
        game_id = as_optional_text(raw.get("game_id"))
        task_prompt = as_text(raw.get("task_prompt"))
        missing = []
        if not task_id:
            missing.append("task_id")
        if not game_id:
            missing.append("game_id")
        if not task_prompt.strip():
            missing.append("task_prompt")
        if missing:
            raise ValueError(f"Task YAML missing required field(s): {', '.join(missing)}")

        match = TASK_ID_PATTERN.fullmatch(task_id)
        if not match or not task_id.startswith(expected_task_id_prefix(game_id)):
            raise ValueError(
                f"Task id {task_id!r} must be "
                f"{expected_task_id_prefix(game_id)}<NN>, numbered within its game."
            )

        difficulty = as_optional_text(raw.get("difficulty")) or DEFAULT_DIFFICULTY
        if difficulty not in DIFFICULTY_MAX_STEPS:
            raise ValueError(
                f"Task {task_id} has unknown difficulty {difficulty!r}; expected one of "
                + ", ".join(sorted(DIFFICULTY_MAX_STEPS))
            )

        return cls(
            task_id=task_id,
            game_id=game_id,
            task_prompt=task_prompt,
            game_url_suffix=as_optional_text(raw.get("game_url_suffix")),
            evaluator_id=as_optional_text(raw.get("evaluator_id")) or "noop",
            evaluator_config=as_mapping(raw.get("evaluator_config")),
            task_start_score_field=as_optional_float(raw.get("task_start_score_field")) or 0.0,
            task_target_score_field=as_optional_float(raw.get("task_target_score_field")),
            pause_during_inference=as_bool(raw.get("pause_during_inference"), default=True),
            continue_on_fail=as_bool(raw.get("continue_on_fail"), default=True),
            difficulty=difficulty,
        )
