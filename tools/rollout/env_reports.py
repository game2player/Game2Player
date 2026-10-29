"""Player observations of suspected problems, sent to the environment builder.

A model collecting with `report_tool: true` may end an episode with report_broken;
the episode is kept (outcome env_reported) with its reason and what the recording
says about it (how long the screen and the task progress had not changed). Reports
of one game become one builder request in the format `environment_factory build
--request` reads, once enough reports have been recorded.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

REPORTED = "env_reported"


def system_report_path(root: Path, episode_id: str) -> Path:
    """Diagnostics live outside episode folders and are never training episodes."""
    return root / "_environment_reports" / episode_id / "report.json"


def system_reports(root: Path) -> list[dict[str, Any]]:
    reports = []
    for path in sorted((root / "_environment_reports").glob("*/report.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        reports.append({**data, "episode": str(path.parent), "source": "system"})
    return reports


def reported_episodes(root: Path) -> dict[str, list[dict[str, Any]]]:
    by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in system_reports(root):
        by_game[report["game_id"]].append(report)
    for path in sorted(root.rglob("summary.json")):
        if "_scheduler" in path.parts:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (data.get("result") or {}).get("outcome") != REPORTED:
            continue
        by_game[str(data.get("game_id"))].append({
            "episode": str(path.parent),
            "task": (data.get("task") or {}).get("id"),
            "seed": data.get("random_seed"),
            "model": (data.get("actor") or {}).get("model_id"),
            **(data.get("report") or {}),
        })
    return by_game


def builder_brief(game_id: str, reports: list[dict[str, Any]]) -> str:
    lines = [
        f"Players or the runtime reported suspected problems in {game_id} in {len(reports)} attempts: a "
        "control that does not do what the guide says, a screen they could not use, or a "
        "possibly broken game. These are observations, not environment verdicts. "
        "Check each report from the players' side, then fix what holds in "
        "the environment (the game build, its instrumentation or the catalog guide); do "
        "not change the game's rules or the task definitions. A report that does not hold "
        "up needs a concrete explanation and player-side check, not just a claim "
        "that the model played badly. If the observation cannot be checked, leave "
        "it unresolved rather than calling the environment broken or verified.",
        "",
        "Reports:",
    ]
    for item in reports:
        if item.get("source") == "system":
            lines.append(
                f"- System report {item['episode']}/report.json (task {item['task']}, "
                f"seed {item['seed']}, phase {item['phase']}, actions {item['step']}): "
                f"{item['reason']}. Last state: {json.dumps(item.get('state'), ensure_ascii=False)}. "
                "Inspect the report and its screenshot/error log. Reproduce with this exact "
                "task and seed through BrowserGameManager. Fix the game adapter or package, "
                "not the framework time limit, task, seed or success criteria. An interactive "
                "menu/bonus screen must be actionable; do not bypass it by granting progress."
            )
            continue
        lines.append(
            f"- {item['episode']} (task {item['task']}, seed {item['seed']}, step {item.get('step')}): "
            f"{item.get('reason')!r}; screen unchanged for {item.get('screen_unchanged_steps')} "
            f"steps, task progress unchanged for {item.get('progress_unchanged_steps')}."
        )
    lines += [
        "",
        "Each episode's inputs.jsonl logs every decision (agent_decision: the action and "
        "when it was observed) and its summary.json the random seed, so replaying those "
        "decisions with the same seed reproduces the run; video.webm shows what happened. "
        "System reports retain a screenshot and available input/state logs instead of a video. "
        "The runtime's timeout is an observation, not proof of the root cause. For a system "
        "report, verify the failed init/reset operation, not task completion.",
    ]
    return "\n".join(lines)


def builder_requests(root: Path, *, min_episodes: int, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "game_id": game_id,
            "builder_brief": builder_brief(game_id, reports),
            "suggested_builder_run_id": f"reported-{game_id}",
            "reports": reports,
        }
        for game_id, items in sorted(reported_episodes(root).items())
        if (reports := [item for item in items if item["episode"] not in (exclude or set())])
        if len(reports) >= min_episodes
    ]


__all__ = ["builder_brief", "builder_requests", "reported_episodes"]
