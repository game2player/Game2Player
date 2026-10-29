"""Run plans: what a rollout runs, independent of which model runs it.

A plan is a YAML file. `mode: eval` runs a fixed list of cases once per seed;
`mode: collect` keeps recording until every task has `target_per_task` valid
episodes, giving a task up once `give_up_after` of its attempts ended without a single
success (a scripted policy that cannot do it). The model is never named here: it is
the input of a run.

    name: evaluation
    mode: eval
    games: all
    tasks: ["01", "03", "05"]       # task-id suffixes per game, or all
    seeds: [67]
    max_steps: 100
    inference: paused
    harness: {history_steps: 50, images_max: 6}  # VLM player settings
    thinking: off                   # optional; otherwise the model profile's first level
    sampling: {temperature: 1.0, seed: episode}

Game groups are reported separately. A game whose catalog lacks a listed task is
skipped for that task, and the skip is reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from catalog import list_games, list_tasks
from catalog.models._base import thinking_level

REPO = Path(__file__).resolve().parents[2]
MODES = {"eval", "collect"}
INFERENCE_MODES = {"paused", "realtime"}
COMMON_FIELDS = {
    "name", "mode", "games", "tasks", "max_steps", "duration_s", "inference", "harness",
    "thinking", "sampling", "report_tool", "continue_on_fail",
}
EVAL_FIELDS = {"seeds"}
COLLECT_FIELDS = {"target_per_task", "seed_namespace", "watchdog", "reuse_root", "give_up_after"}
WATCHDOG_FIELDS = {"progress_stall_s", "adaptive_stall_cap_s", "max_consecutive_failures"}
# Settings of the harness itself; thinking, sampling and the report tool are plan fields.
HARNESS_FIELDS = {"history_steps", "images_max", "review_every"}


@dataclass(frozen=True)
class Watchdog:
    """Stop an episode whose task progress stalls or that keeps failing."""

    progress_stall_s: float = 60.0
    adaptive_stall_cap_s: float = 300.0
    max_consecutive_failures: int = 5


@dataclass(frozen=True)
class Case:
    game_id: str
    task_id: str
    group: str


@dataclass(frozen=True)
class Plan:
    name: str
    mode: str
    groups: dict[str, list[str]] | None  # None: every catalog game
    tasks: list[str] | None              # None: every task of a game
    seeds: list[int] = field(default_factory=list)
    max_steps: int | None = None
    duration_s: float | None = None
    inference: str | None = None
    harness: dict[str, Any] = field(default_factory=dict)
    thinking: str | None = None
    sampling: dict[str, Any] = field(default_factory=dict)
    report_tool: bool = False
    continue_on_fail: bool | None = None
    target_per_task: int = 0
    seed_namespace: str | None = None
    watchdog: Watchdog | None = None
    reuse_root: Path | None = None
    # Stop collecting a task once this many of its attempts ended without one success.
    give_up_after: int | None = None
    path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def group_games(self, groups: list[str]) -> set[str]:
        """The games in the named groups (an unknown group is an error)."""
        known = self.groups or {}
        missing = [name for name in groups if name not in known]
        if missing:
            raise ValueError(f"plan {self.name} has no game group {', '.join(missing)}")
        return {game for name in groups for game in known[name]}

    def cases(self, games: list[str] | None = None) -> tuple[list[Case], list[tuple[str, str]]]:
        """Every (game, task) the plan runs, in plan order, and the (game, task suffix)
        pairs skipped because the game has no such task. `games` restricts the plan
        to a subset (the games a policy exists for, say)."""
        if self.groups is None:
            grouped = [("all", game) for game in list_games()]
        else:
            grouped = [(group, game) for group, members in self.groups.items() for game in members]
        cases: list[Case] = []
        skipped: list[tuple[str, str]] = []
        for group, game in grouped:
            if games is not None and game not in games:
                continue
            available = list_tasks(game)
            if self.tasks is None:
                cases.extend(Case(game, task, group) for task in available)
                continue
            prefix = game.rsplit("/", 1)[-1].split("_", 1)[0]
            for suffix in self.tasks:
                task = f"{prefix}_{suffix}"
                if task in available:
                    cases.append(Case(game, task, group))
                else:
                    skipped.append((game, suffix))
        return cases, skipped


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_plan(path: Path | str) -> Plan:
    path = Path(path)
    if not path.is_absolute() and not path.exists():
        path = REPO / path
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _require(isinstance(raw, dict), f"{path}: a plan is a mapping")
    mode = raw.get("mode")
    _require(mode in MODES, f"{path}: mode must be one of {sorted(MODES)}")
    allowed = COMMON_FIELDS | (EVAL_FIELDS if mode == "eval" else COLLECT_FIELDS)
    unknown = sorted(set(raw) - allowed)
    _require(not unknown, f"{path}: unknown {mode} plan field(s): {', '.join(unknown)}")
    name = str(raw.get("name") or "").strip()
    _require(bool(name), f"{path}: a plan needs a name")

    known_games = set(list_games())
    games = raw.get("games")
    groups: dict[str, list[str]] | None
    if games == "all":
        groups = None
    else:
        _require(isinstance(games, dict) and games, f"{path}: games must be groups of game ids, or all")
        groups = {}
        seen: dict[str, str] = {}
        for group, members in games.items():
            _require(isinstance(members, list) and members, f"{path}: game group {group} is empty")
            for game in members:
                _require(game in known_games, f"{path}: unknown game {game}")
                _require(game not in seen, f"{path}: {game} is in groups {seen.get(game)} and {group}")
                seen[game] = str(group)
            groups[str(group)] = [str(game) for game in members]

    tasks_raw = raw.get("tasks", "all")
    tasks = None if tasks_raw == "all" else [str(task) for task in tasks_raw]
    _require(tasks is None or bool(tasks), f"{path}: tasks must be a list of suffixes, or all")

    harness = dict(raw.get("harness") or {})
    unknown = sorted(set(harness) - HARNESS_FIELDS)
    _require(not unknown, f"{path}: unknown harness field(s): {', '.join(unknown)}")
    inference = raw.get("inference")
    _require(inference is None or inference in INFERENCE_MODES, f"{path}: inference must be paused or realtime")
    max_steps = raw.get("max_steps")
    _require(max_steps is None or (isinstance(max_steps, int) and max_steps > 0), f"{path}: max_steps must be a positive integer")
    duration_s = raw.get("duration_s")
    _require(duration_s is None or float(duration_s) > 0, f"{path}: duration_s must be positive")

    seeds: list[int] = []
    target = 0
    watchdog = None
    reuse_root = None
    give_up_after = None
    if mode == "eval":
        seeds = [int(seed) for seed in raw.get("seeds") or []]
        _require(bool(seeds), f"{path}: an eval plan needs seeds")
        _require(len(set(seeds)) == len(seeds), f"{path}: repeated seed")
    else:
        target = int(raw.get("target_per_task") or 0)
        _require(target > 0, f"{path}: a collect plan needs target_per_task > 0")
        if raw.get("watchdog") is not None:
            values = dict(raw["watchdog"])
            unknown = sorted(set(values) - WATCHDOG_FIELDS)
            _require(not unknown, f"{path}: unknown watchdog field(s): {', '.join(unknown)}")
            watchdog = Watchdog(**values)
            _require(
                watchdog.progress_stall_s > 0 and watchdog.adaptive_stall_cap_s >= watchdog.progress_stall_s,
                f"{path}: stall thresholds must be positive and cap >= base",
            )
            _require(watchdog.max_consecutive_failures >= 1, f"{path}: max_consecutive_failures must be >= 1")
        if raw.get("reuse_root"):
            reuse_root = Path(raw["reuse_root"])
            reuse_root = reuse_root if reuse_root.is_absolute() else REPO / reuse_root
        if raw.get("give_up_after") is not None:
            give_up_after = int(raw["give_up_after"])
            _require(give_up_after >= 1, f"{path}: give_up_after must be >= 1")

    return Plan(
        name=name,
        mode=mode,
        groups=groups,
        tasks=tasks,
        seeds=seeds,
        max_steps=max_steps,
        duration_s=float(duration_s) if duration_s is not None else None,
        inference=inference,
        harness=harness,
        thinking=thinking_level(raw["thinking"]) if raw.get("thinking") is not None else None,
        sampling=dict(raw.get("sampling") or {}),
        report_tool=bool(raw.get("report_tool", False)),
        continue_on_fail=raw.get("continue_on_fail"),
        target_per_task=target,
        seed_namespace=raw.get("seed_namespace"),
        watchdog=watchdog,
        reuse_root=reuse_root,
        give_up_after=give_up_after,
        path=path.resolve(),
        raw=raw,
    )


__all__ = ["Case", "Plan", "Watchdog", "load_plan"]
