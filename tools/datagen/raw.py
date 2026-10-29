"""Read-only access to raw recorded episodes (summary, inputs, states, video)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from harnesses.actions import DEFAULT_ACTION_DURATION

REQUIRED_FILES = ("summary.json", "inputs.jsonl", "states.jsonl", "video.webm")
VALID_OUTCOMES = {"success", "fail", "timeout"}


def number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


@dataclass(frozen=True)
class RawEpisode:
    path: Path
    source: Path
    episode_id: str
    game_id: str
    task_id: str
    instruction: str
    actor: str
    outcome: str
    video_offset_s: float
    video_duration_s: float
    controls: dict[str, Any]
    invalid: str | None
    model: str = ""
    seed: int | None = None

    def load_inputs(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path / "inputs.jsonl")

    def load_states(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path / "states.jsonl")

    def controls_namespace(self) -> SimpleNamespace:
        return SimpleNamespace(
            allowed_keys=set(self.controls["allowed_keys"]),
            hold_duration=float(self.controls["hold_duration"]),
            allow_clicks=bool(self.controls["allow_clicks"]),
        )


def _invalid_reason(path: Path, summary: dict[str, Any]) -> str | None:
    """The collector's rule for a usable episode."""
    if summary.get("schema_version") != 2:
        return "schema_version"
    if summary.get("error"):
        return "error"
    if str((summary.get("result") or {}).get("outcome") or "").lower() not in VALID_OUTCOMES:
        return "outcome"
    if number(((summary.get("video") or {}).get("probe") or {}).get("format", {}).get("duration")) <= 0:
        return "video_probe"
    if number((summary.get("states") or {}).get("coverage")) < 0.9:
        return "state_coverage"
    for name in REQUIRED_FILES:
        file = path / name
        if not file.is_file() or file.stat().st_size == 0:
            return f"missing_{name}"
    return None


def load_episode(path: Path, source: Path) -> RawEpisode:
    summary = read_json(path / "summary.json")
    task = summary.get("task") if isinstance(summary.get("task"), dict) else {}
    roles = (summary.get("catalog") or {}).get("roles") or []
    controls = (roles[0].get("controls") if roles else {}) or {}
    probe = ((summary.get("video") or {}).get("probe") or {}).get("format") or {}
    return RawEpisode(
        path=path,
        source=source,
        episode_id=str(summary.get("episode_id") or path.name),
        game_id=str(summary.get("game_id") or path.parent.name),
        task_id=str(task.get("id") or ""),
        instruction=str(task.get("instruction") or "").strip(),
        actor=str((summary.get("actor") or {}).get("kind") or ""),
        outcome=str((summary.get("result") or {}).get("outcome") or "unknown").lower(),
        video_offset_s=number((summary.get("timing") or {}).get("video_time_offset_s")),
        video_duration_s=number(probe.get("duration")),
        controls={
            "allowed_keys": sorted(controls.get("allowed_keys") or []),
            "hold_duration": number(controls.get("hold_duration"), DEFAULT_ACTION_DURATION)
            or DEFAULT_ACTION_DURATION,
            "allow_clicks": bool(controls.get("allow_clicks", True)),
        },
        invalid=_invalid_reason(path, summary),
        model=str((summary.get("actor") or {}).get("model") or ""),
        seed=summary.get("random_seed"),
    )


def index_sources(sources: list[Path], games: set[str]) -> list[RawEpisode]:
    episodes = []
    for source in sources:
        for game in sorted(games):
            game_dir = source / game
            if not game_dir.is_dir():
                continue
            for summary in sorted(game_dir.glob("*/summary.json")):
                episodes.append(load_episode(summary.parent, source))
    return episodes
