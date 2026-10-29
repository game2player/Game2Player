"""Resume-safe, bounded-concurrency episode scheduling for eval and collect plans.

Collect: every (game, task) is filled up to the plan's target of valid episodes.
Valid episodes already under the reuse root count toward the target, so a run only
records what is missing and an interrupted run resumes where it stopped. Episode seeds
derive from the seed namespace, the game, the task and the episode's ordinal, so one
namespace always replays the same game instances. A watchdog (when the plan has one)
stops an episode whose task progress stalls or that keeps failing; such slots go to a
skip ledger and are not recorded again, and neither are slots whose game the model
reported broken. With `give_up_after`, a task whose attempts so far (valid episodes and
skipped slots) reach that number without one success gets no more episodes; every
task's first attempts are scheduled ahead of all later ones so this is known early.

Eval: every (game, task, seed) of the plan is run until it has one valid episode.

Each episode is one `play.py run` process. An execution or recording error pauses
that game; only an individual model request is retried, inside the player client.
GAMEENV_WEBGL_GPUS can list comma-separated PCI bus ids to spread browser workers
round-robin; without it, the existing GAMEENV_WEBGL_GPU setting is inherited.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import shutil
import signal
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.types import ModelRequestError
from catalog.tasks import load_task

from .plan import Plan, Watchdog
from .env_reports import REPORTED, system_report_path
from .serve import log

REPO = Path(__file__).resolve().parents[2]
VALID_OUTCOMES = {"success", "fail", "timeout"}
# An episode ends itself this long before the scheduler's limit, which leaves time to
# start the browser, finish the model step in flight and write the recording; the
# scheduler's own limit then only catches an episode that hangs.
WALL_CLOCK_MARGIN_S = 600.0
EPISODE_FILES = ("summary.json", "video.webm", "inputs.jsonl", "states.jsonl")


@dataclass(frozen=True)
class Job:
    game_id: str
    task_id: str
    ordinal: int
    seed: int
    estimated_s: float
    # Seconds without task progress after which the watchdog stops the episode
    # (None: the plan has no watchdog).
    stall_timeout_s: float | None


@dataclass(frozen=True)
class Worker:
    """One episode slot: its ports and, for a model, the profile it calls."""

    index: int
    port: int
    control_port: int
    profile_path: Path | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()


def slot_id(game_id: str, task_id: str, ordinal: int) -> str:
    return f"{game_id}/{task_id}/n{ordinal:02d}"


def exhausted_slot_counts(path: Path) -> Counter[tuple[str, str]]:
    """Skip-ledger slots per (game, task) that were given up because every attempt
    failed to produce a valid episode (a crash, an outage), not because of the game."""
    counts: Counter[tuple[str, str]] = Counter()
    if not path.is_file():
        return counts
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
                key = (str(item["game_id"]), str(item["task_id"]))
                identifier = str(item.get("slot_id") or slot_id(*key, int(item["ordinal"])))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if identifier in seen:
                continue
            seen.add(identifier)
            if str(item.get("reason") or "").startswith(("exhausted_after_", "previously_exhausted")):
                counts[key] += 1
    return counts


def load_skip_counts(path: Path) -> tuple[Counter[tuple[str, str]], set[str]]:
    counts: Counter[tuple[str, str]] = Counter()
    seen: set[str] = set()
    if not path.is_file():
        return counts, seen
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
                game_id = str(item["game_id"])
                task_id = str(item["task_id"])
                ordinal = int(item["ordinal"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            identifier = str(item.get("slot_id") or slot_id(game_id, task_id, ordinal))
            if identifier in seen:
                continue
            seen.add(identifier)
            counts[(game_id, task_id)] += 1
    return counts, seen


def bootstrap_skip_ledger(output_root: Path, ledger_path: Path) -> int:
    """Remember completed artifacts that were deliberately removed and exhausted jobs."""
    _, seen = load_skip_counts(ledger_path)
    events_path = output_root / "_scheduler/events.jsonl"
    if not events_path.is_file():
        return 0

    valid_episodes: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    exhausted: dict[str, dict[str, Any]] = {}
    with events_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
                game_id = str(item["game_id"])
                task_id = str(item["task_id"])
                ordinal = int(item["ordinal"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            identifier = slot_id(game_id, task_id, ordinal)
            if item.get("event") == "finish" and item.get("valid") is True:
                episode_id = str(item.get("episode_id") or "")
                valid_episodes[identifier].append((item, episode_id))
            elif item.get("event") == "exhausted":
                exhausted[identifier] = item

    additions: list[dict[str, Any]] = []
    for identifier, episodes in valid_episodes.items():
        if identifier in seen:
            continue
        present = any(
            episode_id
            and (output_root / str(item["game_id"]) / episode_id / "summary.json").is_file()
            for item, episode_id in episodes
        )
        if present:
            continue
        item, episode_id = episodes[-1]
        additions.append(
            {
                "slot_id": identifier,
                "game_id": str(item["game_id"]),
                "task_id": str(item["task_id"]),
                "ordinal": int(item["ordinal"]),
                "reason": "previously_completed_artifact_removed",
                "episode_id": episode_id,
                "recorded_at": utc_now(),
            }
        )
        seen.add(identifier)

    for identifier, item in exhausted.items():
        if item.get("failure_kind") in {"model", "runtime", "environment"}:
            continue  # A paused execution leaves this slot unfinished.
        if identifier in seen or identifier in valid_episodes:
            continue
        additions.append(
            {
                "slot_id": identifier,
                "game_id": str(item["game_id"]),
                "task_id": str(item["task_id"]),
                "ordinal": int(item["ordinal"]),
                "reason": "previously_exhausted",
                "recorded_at": utc_now(),
            }
        )
        seen.add(identifier)

    for item in additions:
        append_jsonl(ledger_path, item)
    return len(additions)


def summary_key(data: dict[str, Any], kind: str) -> tuple[str, str] | None:
    """(game, task) of an episode recorded by an actor of this kind (policy or vlm)."""
    actor = data.get("actor")
    task = data.get("task")
    if not isinstance(actor, dict) or actor.get("kind") != kind:
        return None
    if not isinstance(task, dict):
        return None
    game_id = data.get("game_id")
    task_id = task.get("id")
    if not isinstance(game_id, str) or not isinstance(task_id, str):
        return None
    return game_id, task_id


def structurally_valid_episode(summary_path: Path, data: dict[str, Any], *,
                               reported_ok: bool = False) -> bool:
    """A complete recording with a usable outcome. `reported_ok` also accepts an episode
    the model ended by reporting the game broken: in an evaluation that is its result,
    scored by the progress it reached, not a failed attempt to be run again."""
    episode_dir = summary_path.parent
    actor = data.get("actor") or {}
    states = data.get("states") or {}
    try:
        steps = int(actor.get("steps") or 0)
        coverage = float(states.get("coverage") or 0.0)
        policy_coverage = float(states.get("policy_coverage") or 0.0)
    except (TypeError, ValueError):
        return False
    result = data.get("result") or {}
    outcome = result.get("outcome")
    if data.get("error") or (outcome not in VALID_OUTCOMES and not (reported_ok and outcome == REPORTED)):
        return False
    if (data.get("timing") or {}).get("video_time_offset_error"):
        return False
    # Some autorunners (notably Cubefield) can clear an easy distance target by
    # simply leaving the default forward motion untouched.  A zero-action
    # successful episode is therefore legitimate; a zero-action timeout is not.
    successful_noop = bool(result.get("task_completed")) or result.get("outcome") == "success"
    if (steps <= 0 and not successful_noop) or coverage < 0.9:
        return False
    # Only a policy reads the policy probe; its observations must be there too.
    if actor.get("kind") == "policy" and policy_coverage < 0.9:
        return False
    return all((episode_dir / name).is_file() and (episode_dir / name).stat().st_size > 0
               for name in EPISODE_FILES)


def reported_episode(data: dict[str, Any]) -> bool:
    """The model said the game is broken, and the recording itself is complete."""
    return (data.get("result") or {}).get("outcome") == REPORTED and not data.get("error")


def scan_existing(
    reuse_root: Path,
    allowed: set[tuple[str, str]],
    kind: str,
    *,
    reported_ok: bool = False,
) -> tuple[
    Counter[tuple[str, str]],
    dict[tuple[str, str], list[float]],
    dict[tuple[str, str], list[float]],
    int,
    dict[tuple[str, str, int], Path],
    Counter[tuple[str, str]],
]:
    """Valid episodes per (game, task), their durations, the successful ones'
    durations, how many matching episodes were invalid, each valid episode by
    (game, task, seed), and the successful episodes per (game, task)."""
    counts: Counter[tuple[str, str]] = Counter()
    successes: Counter[tuple[str, str]] = Counter()
    durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    success_durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    by_seed: dict[tuple[str, str, int], Path] = {}
    invalid = 0
    if not reuse_root.is_dir():
        return counts, durations, success_durations, invalid, by_seed, successes
    for summary_path in reuse_root.rglob("summary.json"):
        if "_scheduler" in summary_path.parts:
            continue
        try:
            data = json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError):
            invalid += 1
            continue
        key = summary_key(data, kind)
        if key not in allowed:
            continue
        if not structurally_valid_episode(summary_path, data, reported_ok=reported_ok):
            invalid += 1
            continue
        counts[key] += 1
        succeeded = (data.get("result") or {}).get("outcome") == "success"
        successes[key] += succeeded
        seed = data.get("random_seed")
        if isinstance(seed, int):
            by_seed[(key[0], key[1], seed)] = summary_path
        try:
            duration = float((data.get("timing") or {}).get("duration_s"))
        except (TypeError, ValueError):
            continue
        if duration > 0:
            durations[key].append(duration)
            if succeeded:
                success_durations[key].append(duration)
    return counts, durations, success_durations, invalid, by_seed, successes


def gives_up(attempts: int, successes: int, after: int | None) -> bool:
    """Whether a collection stops a task: `after` attempts and not one success."""
    return after is not None and successes == 0 and attempts >= after


def stall_timeout_for(
    successes: list[float], *, base_s: float, adaptive_cap_s: float
) -> float:
    """Protect empirically slow successes while keeping unseen/stuck tasks strict."""
    if len(successes) < 3:
        return base_s
    ordered = sorted(value for value in successes if math.isfinite(value) and value > 0)
    if len(ordered) < 3:
        return base_s
    index = max(0, math.ceil(0.90 * len(ordered)) - 1)
    return round(min(adaptive_cap_s, max(base_s, ordered[index] + 15.0)), 3)


def stable_seed(namespace: str, game_id: str, task_id: str, ordinal: int) -> int:
    raw = f"{namespace}:{game_id}:{task_id}:{ordinal}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") & 0x7FFFFFFF


def estimate_s(game_id: str, task_id: str, observed: list[float], plan: Plan, kind: str,
               policies: dict[str, Any]) -> float:
    """How long an episode probably takes, to start long jobs first."""
    if observed:
        return sum(observed) / len(observed)
    if kind == "policy":
        from harnesses.policy.policies import get_policy

        if game_id not in policies:
            policies[game_id] = get_policy(game_id)
        interval = max(0.008, float(getattr(policies[game_id], "step_interval_s", 0.5)))
        max_steps = load_task(game_id, task_id).max_steps
        return min(600.0, max(10.0, float(max_steps or 200) * interval))
    steps = plan.max_steps or load_task(game_id, task_id).max_steps or 200
    return float(steps)


def build_collect_jobs(
    keys: list[tuple[str, str]],
    counts: Counter[tuple[str, str]],
    durations: dict[tuple[str, str], list[float]],
    success_durations: dict[tuple[str, str], list[float]],
    skipped: Counter[tuple[str, str]],
    plan: Plan,
    kind: str,
    seed_namespace: str,
    successes: Counter[tuple[str, str]] | None = None,
    exhausted: Counter[tuple[str, str]] | None = None,
    priority_games: set[str] | None = None,
    task_order: str = "longest-first",
    reverse_games: bool = False,
) -> list[Job]:
    policies: dict[str, Any] = {}
    watchdog = plan.watchdog
    successes = successes or Counter()
    exhausted = exhausted or Counter()
    jobs: list[Job] = []
    for game_id, task_id in keys:
        key = (game_id, task_id)
        if gives_up(counts[key] + skipped[key] - exhausted[key], successes[key], plan.give_up_after):
            continue
        estimate = estimate_s(game_id, task_id, durations.get((game_id, task_id), []), plan, kind, policies)
        have = min(plan.target_per_task, counts[(game_id, task_id)] + skipped[(game_id, task_id)])
        stall_timeout_s = (
            stall_timeout_for(
                success_durations.get((game_id, task_id), []),
                base_s=watchdog.progress_stall_s,
                adaptive_cap_s=watchdog.adaptive_stall_cap_s,
            )
            if watchdog
            else None
        )
        for ordinal in range(have + 1, plan.target_per_task + 1):
            jobs.append(
                Job(
                    game_id=game_id,
                    task_id=task_id,
                    ordinal=ordinal,
                    seed=stable_seed(seed_namespace, game_id, task_id, ordinal),
                    estimated_s=round(estimate, 3),
                    stall_timeout_s=stall_timeout_s,
                )
            )
    # Dynamic workers remove most imbalance; starting likely-long jobs first
    # reduces the straggler tail at the end of the run. A task's first attempts go
    # ahead of every later one, so a task to give up is known before most of its
    # episodes would start.
    probe = plan.give_up_after
    task_rounds: dict[tuple[str, str], int] = {}
    if task_order == "round-robin":
        tasks_by_game: dict[str, set[str]] = {}
        for game_id, task_id in keys:
            tasks_by_game.setdefault(game_id, set()).add(task_id)
        task_rounds = {(game_id, task_id): index
                       for game_id, task_ids in tasks_by_game.items()
                       for index, task_id in enumerate(sorted(task_ids))}
    game_ranks: dict[str, int] = {}
    if reverse_games:
        # Reverse the whole game sequence, including the priority group, while
        # preserving task rounds and every job's identity and seed.
        games = sorted({game_id for game_id, _ in keys},
                       key=lambda game: (bool(priority_games) and game not in priority_games, game),
                       reverse=True)
        game_ranks = {game_id: index for index, game_id in enumerate(games)}
    jobs.sort(key=lambda item: (task_rounds.get((item.game_id, item.task_id), 0),
                                game_ranks.get(item.game_id, 0),
                                bool(priority_games) and item.game_id not in priority_games,
                                probe is not None and item.ordinal > probe, -item.estimated_s,
                                item.game_id, item.task_id, item.ordinal))
    return jobs


def build_eval_jobs(
    keys: list[tuple[str, str]],
    plan: Plan,
    kind: str,
    done: dict[tuple[str, str, int], Path],
    durations: dict[tuple[str, str], list[float]],
) -> list[Job]:
    policies: dict[str, Any] = {}
    jobs = [
        Job(
            game_id=game_id,
            task_id=task_id,
            ordinal=ordinal,
            seed=seed,
            estimated_s=round(estimate_s(game_id, task_id, durations.get((game_id, task_id), []),
                                         plan, kind, policies), 3),
            stall_timeout_s=None,
        )
        for game_id, task_id in keys
        for ordinal, seed in enumerate(plan.seeds, start=1)
        if (game_id, task_id, seed) not in done
    ]
    jobs.sort(key=lambda item: (-item.estimated_s, item.game_id, item.task_id, item.ordinal))
    return jobs


class LiveEpisodeGuard:
    """Incrementally watch task signals without rereading a growing states file."""

    def __init__(self, stall_timeout_s: float, max_failures: int):
        self.stall_timeout_s = stall_timeout_s
        self.max_failures = max_failures
        self.offset = 0
        self.partial = b""
        self.latest_t: float | None = None
        self.last_gain_t: float | None = None
        self.best: dict[str, float] = {}
        self.counter_last: dict[str, float] = {}
        self.counter_totals: dict[str, float] = {}
        self.failure_events = 0
        self.failure_anchor = 0.0
        self.in_failure = False
        self.finished_successfully = False

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            return numeric if math.isfinite(numeric) else None
        return None

    def _consume(self, item: dict[str, Any]) -> None:
        current_t = self._number(item.get("t"))
        if current_t is None:
            return
        self.latest_t = current_t
        if self.last_gain_t is None:
            self.last_gain_t = current_t

        task = item.get("task") if isinstance(item.get("task"), dict) else {}
        metrics = task.get("metrics") if isinstance(task.get("metrics"), dict) else {}
        signals = {
            "progress": task.get("progress"),
            "score_best": task.get("score_best"),
            "progress_best": metrics.get("progress_best"),
            "score_run_best": metrics.get("score_run_best"),
        }
        gained = False
        for name, raw in signals.items():
            value = self._number(raw)
            if value is None:
                continue
            previous = self.best.get(name)
            if previous is None:
                self.best[name] = value
            elif value > previous + 1e-9:
                self.best[name] = value
                gained = True
        if gained:
            self.last_gain_t = current_t

        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        policy_state = (
            item.get("policy_state") if isinstance(item.get("policy_state"), dict) else {}
        )
        terminal = state.get("terminal") if isinstance(state.get("terminal"), dict) else {}
        failure = bool(task.get("should_reset")) or task.get("status") == "fail" or (
            terminal.get("isTerminal") is True and terminal.get("outcome") == "fail"
        ) or policy_state.get("dying") is True
        if failure and not self.in_failure:
            self.failure_events += 1
        self.in_failure = failure

        # Many games auto-respawn without emitting evaluator terminal/fail.  Their
        # trusted or policy state instead exposes cumulative deaths/attempts.
        counter_roots = {
            "state.metrics": state.get("metrics"),
            "state.game_state": state.get("game_state"),
            "policy_state": policy_state,
            "task.metrics": metrics,
        }
        counter_names = ("death", "deaths", "death_count", "failures", "losses", "attempts")
        for root_name, root in counter_roots.items():
            if not isinstance(root, dict):
                continue
            for raw_name, raw_value in root.items():
                name = str(raw_name).lower().split(".")[-1]
                if name not in counter_names:
                    continue
                value = self._number(raw_value)
                if value is None:
                    continue
                signal_name = f"{root_name}.{raw_name}"
                previous = self.counter_last.get(signal_name)
                self.counter_last[signal_name] = value
                if previous is None:
                    self.counter_totals.setdefault(signal_name, 0.0)
                elif value >= previous:
                    self.counter_totals[signal_name] += value - previous
                else:
                    # Counter reset after a respawn/reload; retain accumulated deaths.
                    self.counter_totals[signal_name] += max(0.0, value)

        failure_total = max([float(self.failure_events), *self.counter_totals.values()])
        if gained:
            self.failure_anchor = failure_total
        self.finished_successfully = (
            self.finished_successfully
            or task.get("status") == "success"
            or task.get("completed") is True
            or (terminal.get("isTerminal") is True and terminal.get("outcome") == "success")
        )

    def poll(self, states_path: Path) -> str | None:
        if not states_path.is_file():
            return None
        try:
            with states_path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
                self.offset = handle.tell()
        except OSError:
            return None
        if chunk:
            payload = self.partial + chunk
            lines = payload.split(b"\n")
            self.partial = lines.pop()
            for raw in lines:
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(item, dict):
                    self._consume(item)

        if self.finished_successfully:
            return None
        failure_total = max([float(self.failure_events), *self.counter_totals.values()])
        consecutive_failures = int(failure_total - self.failure_anchor)
        if consecutive_failures >= self.max_failures:
            return f"consecutive_failures={consecutive_failures}"
        if (
            self.latest_t is not None
            and self.last_gain_t is not None
            and self.latest_t - self.last_gain_t >= self.stall_timeout_s
        ):
            stalled = self.latest_t - self.last_gain_t
            return f"progress_stall={stalled:.1f}s threshold={self.stall_timeout_s:.1f}s"
        return None


class Scheduler:
    def __init__(
        self,
        *,
        plan: Plan,
        kind: str,
        output_root: Path,
        jobs: list[Job],
        workers: list[Worker],
        episode_prefix: str,
        plan_copy: Path,
        harness_config: Path | None,
        job_timeout_s: float,
        reused: int,
        skipped_at_start: int,
        task_count: int,
        invalid_existing: int,
        run_info: dict[str, Any],
        task_attempts: Counter[tuple[str, str]] | None = None,
        task_successes: Counter[tuple[str, str]] | None = None,
        given_up_at_start: list[str] | None = None,
        keep_going: bool = False,
    ) -> None:
        self.plan = plan
        self.kind = kind
        self.output_root = output_root
        self.jobs = jobs
        self.workers = workers
        self.episode_prefix = episode_prefix
        self.plan_copy = plan_copy
        self.harness_config = harness_config
        self.job_timeout_s = job_timeout_s
        self.reused = reused
        self.skipped_at_start = skipped_at_start
        self.task_count = task_count
        self.invalid_existing = invalid_existing
        self.run_info = run_info
        self.keep_going = keep_going
        self.collect = plan.mode == "collect"
        self.target = plan.target_per_task if self.collect else len(plan.seeds)
        self.control = output_root / "_scheduler"
        self.status_path = self.control / "status.json"
        self.events_path = self.control / "events.jsonl"
        self.skip_ledger_path = self.control / "skipped_jobs.jsonl"
        _, self.skip_ids = load_skip_counts(self.skip_ledger_path)
        self.fail_logs = self.control / "failed_attempts"
        self.started_at = utc_now()
        self.started_mono = time.monotonic()
        self.succeeded = 0
        self.skipped = 0
        self.reported = 0
        self.failed_attempts = 0
        self.exhausted: list[dict[str, Any]] = []
        self.paused_games: dict[str, str] = {}
        # Attempts (valid episodes and skipped slots) and successes per (game, task),
        # for giving a task up; tasks given up before this run started have no jobs.
        self.task_attempts: Counter[tuple[str, str]] = Counter(task_attempts or {})
        self.task_successes: Counter[tuple[str, str]] = Counter(task_successes or {})
        self.given_up_at_start = sorted(given_up_at_start or [])
        self.given_up: dict[str, dict[str, Any]] = {}
        self.not_run = 0
        self.settled_events: dict[tuple[str, str], asyncio.Event] = {}
        # Each task's first attempts (ordinals up to give_up_after) not yet finished.
        after = plan.give_up_after
        self.probes_pending: Counter[tuple[str, str]] = Counter(
            (job.game_id, job.task_id) for job in jobs if after is not None and job.ordinal <= after)
        self.active: dict[int, dict[str, Any]] = {}
        self.processes: dict[int, asyncio.subprocess.Process] = {}
        self.stop_requested = False
        self.stop_reason: str | None = None
        self.lock_handle: Any = None

    def acquire_lock(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        self.fail_logs.mkdir(parents=True, exist_ok=True)
        self.lock_handle = (self.control / "lock").open("w")
        try:
            fcntl.flock(self.lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another scheduler already holds the output lock") from exc
        self.lock_handle.write(str(os.getpid()) + "\n")
        self.lock_handle.flush()
        (self.control / "pid").write_text(str(os.getpid()) + "\n")

    def status(self, state: str = "running") -> dict[str, Any]:
        elapsed = time.monotonic() - self.started_mono
        processed = self.succeeded + self.skipped + self.reported
        rate = processed / elapsed if elapsed > 0 else 0.0
        remaining = len(self.jobs) - processed - len(self.exhausted) - self.not_run
        eta_s = remaining / rate if rate > 0 else None
        return {
            "state": state,
            "stop_reason": self.stop_reason,
            "pid": os.getpid(),
            "plan": self.plan.name,
            "mode": self.plan.mode,
            "keep_going": self.keep_going,
            **self.run_info,
            "started_at": self.started_at,
            "updated_at": utc_now(),
            "workers": len(self.workers),
            "target_per_task": self.target,
            "task_count": self.task_count,
            "target_total": self.task_count * self.target,
            "reused_at_start": self.reused,
            "skipped_at_start": self.skipped_at_start,
            "scheduled_at_start": len(self.jobs),
            "succeeded_this_run": self.succeeded,
            "skipped_this_run": self.skipped,
            "reported_this_run": self.reported,
            "complete_total": self.reused + self.succeeded,
            "accounted_total": (
                self.reused + self.skipped_at_start + self.succeeded + self.skipped + self.reported
            ),
            "remaining": max(0, remaining),
            "failed_attempts": self.failed_attempts,
            "exhausted_jobs": self.exhausted,
            "paused_games": self.paused_games,
            "given_up_at_start": self.given_up_at_start,
            "given_up_this_run": self.given_up,
            "not_run_given_up": self.not_run,
            "invalid_existing_ignored": self.invalid_existing,
            "elapsed_s": round(elapsed, 1),
            "eta_s": round(eta_s, 1) if eta_s is not None else None,
            "active": {str(key): value for key, value in sorted(self.active.items())},
            "output_root": str(self.output_root),
        }

    def record_skip(self, job: Job, *, episode_id: str, reason: str) -> None:
        identifier = slot_id(job.game_id, job.task_id, job.ordinal)
        if identifier in self.skip_ids:
            return
        append_jsonl(
            self.skip_ledger_path,
            {
                "slot_id": identifier,
                "game_id": job.game_id,
                "task_id": job.task_id,
                "ordinal": job.ordinal,
                "reason": reason,
                "episode_id": episode_id,
                "recorded_at": utc_now(),
            },
        )
        self.skip_ids.add(identifier)

    def settled(self, key: tuple[str, str]) -> bool:
        """Whether the task's first attempts have decided if it is given up: one
        succeeded, or all have finished (crashed ones included)."""
        after = self.plan.give_up_after
        return after is None or self.task_successes[key] > 0 or self.probes_pending[key] <= 0

    def settled_event(self, key: tuple[str, str]) -> asyncio.Event:
        event = self.settled_events.setdefault(key, asyncio.Event())
        if self.settled(key) or self.stop_requested:
            event.set()
        return event

    def note_attempt(self, job: Job, *, success: bool) -> None:
        """Count a finished attempt toward its task, and give the task up when it is due."""
        key = (job.game_id, job.task_id)
        self.task_attempts[key] += 1
        self.task_successes[key] += success
        self.settled_event(key)
        name = f"{job.game_id}/{job.task_id}"
        if name in self.given_up or not self.collect:
            return
        if gives_up(self.task_attempts[key], self.task_successes[key], self.plan.give_up_after):
            self.given_up[name] = {"attempts": self.task_attempts[key], "at": utc_now()}
            append_jsonl(self.events_path, {"event": "given_up", "at": utc_now(), "game_id": job.game_id,
                                            "task_id": job.task_id, "attempts": self.task_attempts[key]})
            log(f"given up {name}: {self.task_attempts[key]} attempts without a success")

    def write_status(self, state: str = "running") -> None:
        atomic_json(self.status_path, self.status(state))

    def request_stop(self, reason: str = "signal") -> None:
        if self.stop_requested:
            return
        self.stop_requested = True
        self.stop_reason = reason
        for event in self.settled_events.values():
            event.set()
        log(f"stop requested ({reason}); terminating active episode processes")
        for process in list(self.processes.values()):
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def episode_id(self, job: Job, attempt: int) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        game_name = job.game_id.replace("/", "_")
        if self.collect:
            # Include the collection slot and attempt in the episode name.
            return (f"{self.episode_prefix}_{game_name}_{job.task_id}_n{job.ordinal:02d}_"
                    f"a{attempt}_{stamp}")
        return f"{self.episode_prefix}_{game_name}_{job.task_id}_s{job.seed}_a{attempt}_{stamp}"

    def command(self, worker: Worker, job: Job, episode_id: str) -> list[str]:
        command = [
            sys.executable,
            str(REPO / "play.py"),
            "run",
            "--game",
            job.game_id,
            "--task",
            job.task_id,
            "--actor",
            "policy" if self.kind == "policy" else "vlm",
            "--headless",
            "--output-root",
            str(self.output_root),
            "--episode-id",
            episode_id,
            "--plan",
            str(self.plan_copy),
            "--port",
            str(worker.port),
            "--control-port",
            str(worker.control_port),
            "--random-seed",
            str(job.seed),
        ]
        if worker.profile_path is not None:
            command += ["--model", str(worker.profile_path)]
        if self.harness_config is not None:
            command += ["--actor-config", str(self.harness_config)]
        if self.plan.max_steps is not None:
            command += ["--max-steps", str(self.plan.max_steps)]
        if self.plan.duration_s is not None:
            command += ["--duration-s", str(self.plan.duration_s)]
        command += ["--wall-clock-s", f"{max(60.0, self.job_timeout_s - WALL_CLOCK_MARGIN_S):g}"]
        if self.plan.inference is not None:
            command += ["--inference-mode", self.plan.inference]
        if self.plan.continue_on_fail is False:
            command.append("--no-continue-on-fail")
        return command

    async def run_attempt(self, worker: Worker, job: Job, attempt: int) -> tuple[str, str, str | None]:
        episode_id = self.episode_id(job, attempt)
        episode_dir = self.output_root / job.game_id / episode_id
        command = self.command(worker, job, episode_id)
        env = dict(os.environ)
        render_devices = [device.strip() for device in env.get("GAMEENV_WEBGL_GPUS", "").split(",")
                          if device.strip()]
        if render_devices:
            env["GAMEENV_WEBGL_GPU"] = render_devices[worker.index % len(render_devices)]
        # Trajectory finalization calls ffprobe by name; resolve it from this
        # interpreter's environment even when the shell has not activated it.
        interpreter_bin = str(Path(sys.executable).resolve().parent)
        env["PATH"] = interpreter_bin + os.pathsep + env.get("PATH", "")
        self.active[worker.index] = {
            **asdict(job),
            "attempt": attempt,
            "episode_id": episode_id,
            "started_at": utc_now(),
        }
        self.write_status()
        append_jsonl(
            self.events_path,
            {"event": "start", "at": utc_now(), "worker": worker.index, **self.active[worker.index]},
        )
        game_name = job.game_id.replace("/", "_")
        failure_name = f"w{worker.index}_{game_name}_{job.task_id}_n{job.ordinal:02d}_a{attempt}.log"
        failure_log = self.fail_logs / failure_name
        # The child writes here directly: CLI diagnostics stay visible while a
        # request is in flight, rather than being buffered until the episode ends.
        with failure_log.open("wb") as log_file:
            log_file.write(f"command: {' '.join(command)}\n\n".encode())
            log_file.flush()
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=REPO,
                env=env,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        self.processes[worker.index] = process
        communicate_task = asyncio.create_task(process.communicate())
        guard = (
            LiveEpisodeGuard(
                stall_timeout_s=job.stall_timeout_s,
                max_failures=self.plan.watchdog.max_consecutive_failures,
            )
            if self.plan.watchdog is not None and job.stall_timeout_s is not None
            else None
        )
        abort_kind: str | None = None
        abort_reason: str | None = None
        deadline = time.monotonic() + self.job_timeout_s
        try:
            while True:
                try:
                    output, _ = await asyncio.wait_for(
                        asyncio.shield(communicate_task), timeout=2.0
                    )
                    break
                except asyncio.TimeoutError:
                    if time.monotonic() >= deadline:
                        abort_kind = "runtime_error"
                        abort_reason = "episode process exceeded scheduler timeout"
                    elif guard is not None and not self.stop_requested:
                        watchdog_reason = guard.poll(episode_dir / "states.jsonl")
                        if watchdog_reason is not None:
                            abort_kind = "skip"
                            abort_reason = watchdog_reason
                    if abort_kind is None:
                        continue

                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        output, _ = await asyncio.wait_for(
                            asyncio.shield(communicate_task), timeout=10.0
                        )
                    except asyncio.TimeoutError:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        output, _ = await communicate_task
                    break
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            self.processes.pop(worker.index, None)
        valid = False
        reported = False
        outcome = None
        reason = abort_reason or f"exit_code={process.returncode}"
        model_error = False
        environment_error = False
        report_path = system_report_path(self.output_root, episode_id)
        if abort_kind is None and report_path.is_file():
            report = json.loads(report_path.read_text())
            environment_error = (report.get("game_id"), report.get("task"), report.get("seed")) == (
                job.game_id, job.task_id, job.seed)
            if environment_error:
                reason = f"{report['phase']}: {report['reason']} (report: {report_path})"
        summary_path = episode_dir / "summary.json"
        if abort_kind is None and not environment_error and summary_path.is_file():
            try:
                data = json.loads(summary_path.read_text())
                matches = summary_key(data, self.kind) == (job.game_id, job.task_id)
                model_error = matches and (data.get("result") or {}).get("stop_reason") == ModelRequestError.__name__
                valid = not model_error and process.returncode == 0 and matches and structurally_valid_episode(
                    summary_path, data, reported_ok=not self.collect)
                outcome = (data.get("result") or {}).get("outcome")
                reported = matches and not valid and self.collect and reported_episode(data)
                if model_error:
                    detail = str(data.get("error") or ModelRequestError.__name__).splitlines()[-1]
                    reason = f"model request failed: {detail[:300]}"
                elif valid and outcome == REPORTED:
                    reason = f"valid, {REPORTED}: {(data.get('report') or {}).get('reason')}"
                elif valid:
                    reason = "valid"
                elif reported:
                    reason = f"{REPORTED}: {(data.get('report') or {}).get('reason')}"
                elif (data.get("timing") or {}).get("video_time_offset_error"):
                    reason = f"video alignment failed: {data['timing']['video_time_offset_error']}"
                else:
                    outcome = (data.get("result") or {}).get("outcome")
                    reason = f"invalid episode (outcome={outcome}, schema/coverage/actions)"
            except (OSError, json.JSONDecodeError) as exc:
                reason = f"summary error: {exc}"
        disposition = ("environment_error" if environment_error else "model_error" if model_error else
                       "valid" if valid else ("reported" if reported else (abort_kind or "runtime_error")))
        if disposition in {"skip", "reported"}:
            self.record_skip(job, episode_id=episode_id, reason=reason)
        elif not valid:
            self.failed_attempts += 1
        if valid or disposition in {"skip", "reported"}:
            failure_log.unlink(missing_ok=True)
        else:
            with failure_log.open("a") as log_file:
                log_file.write(f"\nreason: {reason}\n")
        if disposition not in {"valid", "reported"}:
            # A skipped/invalid episode is deliberately not retained, including its video.
            # A reported one is: it is the evidence the environment builder needs.
            if episode_dir.is_dir() and episode_dir.resolve().is_relative_to(
                self.output_root.resolve()
            ):
                shutil.rmtree(episode_dir)
        append_jsonl(
            self.events_path,
            {
                "event": "finish",
                "at": utc_now(),
                "worker": worker.index,
                **asdict(job),
                "attempt": attempt,
                "episode_id": episode_id,
                "valid": valid,
                "disposition": disposition,
                "outcome": outcome,
                "reason": reason,
                "returncode": process.returncode,
            },
        )
        self.active.pop(worker.index, None)
        self.write_status()
        return disposition, reason, outcome

    async def worker(self, worker: Worker, queue: asyncio.Queue[Job | None]) -> None:
        while True:
            job = await queue.get()
            try:
                if job is None:
                    return
                if self.stop_requested:
                    return
                if job.game_id in self.paused_games:
                    continue
                key = (job.game_id, job.task_id)
                if (self.collect and self.plan.give_up_after is not None
                        and job.ordinal > self.plan.give_up_after and not self.settled(key)):
                    # A later attempt waits for the task's first ones, which all started
                    # before it, to show whether the task is given up.
                    await self.settled_event(key).wait()
                    if self.stop_requested:
                        return
                if f"{job.game_id}/{job.task_id}" in self.given_up:
                    self.not_run += 1
                    continue
                if self.stop_requested:
                    return
                if job.game_id in self.paused_games:
                    continue
                outcome = None
                try:
                    disposition, reason, outcome = await self.run_attempt(worker, job, 1)
                except Exception as exc:
                    disposition = "runtime_error"
                    reason = f"scheduler exception: {exc}"
                    self.failed_attempts += 1
                    append_jsonl(self.events_path, {
                        "event": "exception", "at": utc_now(), "worker": worker.index,
                        **asdict(job), "attempt": 1, "error": traceback.format_exc(),
                    })
                    self.active.pop(worker.index, None)
                if self.stop_requested:
                    return  # An interrupted slot remains pending on resume.
                if job.game_id in self.paused_games and disposition not in {"valid", "reported"}:
                    continue  # Another worker stopped this game's pending episode.
                if disposition == "valid":
                    self.succeeded += 1
                    self.note_attempt(job, success=outcome == "success")
                    log(f"worker={worker.index} complete={self.reused + self.succeeded}/"
                        f"{self.task_count * self.target} "
                        f"job={job.game_id}/{job.task_id} n={job.ordinal}")
                elif disposition in {"skip", "reported"}:
                    if disposition == "skip":
                        self.skipped += 1
                    else:
                        self.reported += 1
                    self.note_attempt(job, success=False)
                    log(f"worker={worker.index} {disposition} "
                        f"job={job.game_id}/{job.task_id} n={job.ordinal} reason={reason}")
                elif disposition == "model_error" and "[request-rate-limit]" in reason:
                    # A shared provider limit is not a defect in this game. Leave
                    # every unfinished slot pending rather than cycling through games.
                    self.request_stop(f"model rate limit persisted after request retries: {reason}")
                    self.write_status()
                    return
                else:
                    failure = {**asdict(job), "reason": reason,
                               "failure_kind": ("environment" if disposition == "environment_error" else
                                                "model" if disposition == "model_error" else "runtime")}
                    self.exhausted.append(failure)
                    append_jsonl(self.events_path, {"event": "exhausted", "at": utc_now(), **failure})
                    if self.keep_going:
                        log(f"failed {job.game_id}/{job.task_id}: {reason}; continuing other jobs")
                        self.write_status()
                        continue
                    self.paused_games[job.game_id] = reason
                    for key, event in self.settled_events.items():
                        if key[0] == job.game_id:
                            event.set()
                    for index, active in list(self.active.items()):
                        process = self.processes.get(index)
                        if (active["game_id"] == job.game_id and process is not None
                                and process.returncode is None):
                            try:
                                os.killpg(process.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                    log(f"paused {job.game_id}: {reason}")
                self.write_status()
            finally:
                if job is not None and self.plan.give_up_after is not None \
                        and job.ordinal <= self.plan.give_up_after:
                    key = (job.game_id, job.task_id)
                    self.probes_pending[key] -= 1
                    self.settled_event(key)
                queue.task_done()

    async def run(self) -> int:
        self.acquire_lock()
        self.write_status()
        append_jsonl(
            self.events_path,
            {
                "event": "scheduler_start",
                "at": utc_now(),
                "pid": os.getpid(),
                "workers": len(self.workers),
                "reused": self.reused,
                "scheduled": len(self.jobs),
                **self.run_info,
            },
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_stop, sig.name)
        queue: asyncio.Queue[Job | None] = asyncio.Queue()
        for job in self.jobs:
            queue.put_nowait(job)
        for _ in self.workers:
            queue.put_nowait(None)
        tasks = [
            asyncio.create_task(self.worker(worker, queue))
            for worker in self.workers
        ]
        await asyncio.gather(*tasks)
        if self.stop_requested:
            state = "stopped"
            result = 130
        elif self.paused_games:
            state = "paused"
            result = 2
        elif self.exhausted:
            state = "finished_with_failures"
            result = 2
        else:
            state = "complete"
            result = 0
        self.write_status(state)
        append_jsonl(
            self.events_path,
            {"event": "scheduler_finish", "at": utc_now(), "state": state, "stop_reason": self.stop_reason},
        )
        log(
            f"scheduler {state}: reused={self.reused} succeeded={self.succeeded} "
            f"skipped={self.skipped} reported={self.reported} exhausted={len(self.exhausted)}"
        )
        given_up = [*self.given_up_at_start, *self.given_up]
        if given_up:
            log(f"given up (no success in {self.plan.give_up_after} attempts): {', '.join(given_up)}")
        return result


__all__ = [
    "Job",
    "LiveEpisodeGuard",
    "Scheduler",
    "Watchdog",
    "Worker",
    "build_collect_jobs",
    "build_eval_jobs",
    "exhausted_slot_counts",
    "gives_up",
    "load_skip_counts",
    "bootstrap_skip_ledger",
    "scan_existing",
    "stable_seed",
    "structurally_valid_episode",
]
