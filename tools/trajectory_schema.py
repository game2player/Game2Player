"""Canonical trajectory episode contract."""

from __future__ import annotations

import json
from pathlib import Path

EPISODE_FILES = {"summary.json", "video.webm", "inputs.jsonl", "states.jsonl"}


def _validate_jsonl(path: Path, *, states: bool = False) -> None:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
            if states:
                required = {"sample_id", "t", "finalized", "state", "task"}
                missing = required - set(row)
                if missing:
                    raise ValueError(
                        f"state row is missing {sorted(missing)} at {path}:{line_number}"
                    )
                for field in ("state", "policy_state", "task"):
                    value = row.get(field)
                    if value is not None and not isinstance(value, dict):
                        raise ValueError(
                            f"state row {field} must be an object or null at "
                            f"{path}:{line_number}"
                        )


def validate_episode(path: Path) -> None:
    entries = {item.name for item in path.iterdir()}
    if entries != EPISODE_FILES:
        raise ValueError(
            f"episode must contain exactly {sorted(EPISODE_FILES)}; found {sorted(entries)}"
        )
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    if not isinstance(summary, dict) or summary.get("schema_version") != 2:
        raise ValueError("summary.json does not declare schema_version 2")
    if (path / "video.webm").stat().st_size <= 0:
        raise ValueError("video.webm is empty")
    _validate_jsonl(path / "inputs.jsonl")
    _validate_jsonl(path / "states.jsonl", states=True)


__all__ = ["EPISODE_FILES", "validate_episode"]
