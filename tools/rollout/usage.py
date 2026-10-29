"""Token usage accounting for generated game rollouts."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any



def episode_usage(run_dir: Path) -> dict[str, int] | None:
    """Model tokens over every decision recorded under a run directory, summed per
    usage field (nested fields as `prompt_tokens_details.cached_tokens`), with the
    number of decisions; None when no decision recorded any usage."""
    totals: dict[str, int] = defaultdict(int)

    def add(usage: dict[str, Any], prefix: str = "") -> None:
        for key, value in usage.items():
            if isinstance(value, dict):
                add(value, f"{prefix}{key}.")
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[f"{prefix}{key}"] += value

    for path in sorted(run_dir.rglob("inputs.jsonl")):
        if "_scheduler" in path.parts:
            continue
        with path.open(encoding="utf-8") as lines:
            for line in lines:
                if '"usage"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "agent_decision" and isinstance(event.get("usage"), dict):
                    totals["decisions"] += 1
                    add(event["usage"])
    return {key: value for key, value in sorted(totals.items()) if value} or None


__all__ = ["episode_usage"]
