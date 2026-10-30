"""Run a list of new games from Scout to training data, one pipeline per game.

Supply ``games`` directly, or ``scout_prompt`` to find the games in one Scout search.
Every listed game goes through the stages below on its own, at most ``--jobs`` games
at a time; ``--from`` and ``--until`` pick a range:

  scout      Scout inspects the game in a session of its own; a rejected game stops here
  builder    build the environment, including the Builder's check from the players' side
  vlm_check  a model plays every task once; the Builder checks its suspected problems
             and verifies any repair through player inputs. Uncertain reports stay
             ``review_pending`` and may be investigated by the policy author, but
             do not enter collection until reviewed
  policy     write a policy on the checked environment, with the repairs it asks for
  collect    training data from the policy and the model, for games not held out

A local model is served once for the campaign on ``--gpus``, and each game's model
episodes take those servers in turn, so the GPUs carry the same load whichever games
are playing at once. Each game has its own log and progress row under the
campaign directory; the parent assembles ``progress.json``.

With ``--agents claude=2,codex=2`` several campaigns run as one pool: their
games share the servers, the port slots and the agents, each agent up to its number of
games at a time (a game that is only collecting episodes does not count). A new game
takes the first agent in that list with room; a game once started keeps its agent,
whose conversations it is in. An agent that reports its plan's usage limit gets no
games until the limit resets, and its games wait as ``waiting_agent`` meanwhile; an
agent whose CLI fails three games in a row rests for half an hour. Rollout port slots
are claimed with lock files, so separate runs on one machine never share one.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import traceback
from urllib.parse import urlparse

import yaml

from agents.types import ModelRequestError
from catalog.models import load_model
from tools.codex_session import AgentUsageLimitError
from tools.coding_session import CodingProcessError, agent_usage, load_agent
from tools.debug_media import remove_debug_media
from tools.environment_factory.__main__ import _load_build_request
from tools.environment_factory.builder import Builder
from tools.environment_factory.scout import Scout
from tools.environment_factory.specification import load_specification
from tools.policy_author.agent import PolicyAuthor
from tools.rollout.env_reports import builder_requests, system_reports
from tools.rollout.usage import episode_usage
from tools.rollout.serve import gpu_states, port_free, require_idle_gpus, start_server


CODE_ROOT = Path(__file__).resolve().parents[1]
STAGES = ("scout", "builder", "vlm_check", "policy", "collect")
POLL_S = 5.0
# A Ctrl-C reaches every child process directly; this is how long they get to stop
# their agents before the parent terminates them itself.
STOP_GRACE_S = 10.0
# The campaign's model servers listen on this port plus the GPU index, apart from the
# ports a separate tools.rollout run starts its own servers on.
SERVER_PORT_BASE = 8700
# Each game running at the same time plays its rollouts on its own block of ports, so
# two games that reach a rollout together do not collide: the first half of a block
# serves the episodes, the second half their control channels.
ROLLOUT_PORT_BASE = 23000
ROLLOUT_PORT_BLOCK = 100
# A game holds a lock file per slot while it runs, so campaigns started separately on
# one machine never take the same port block.
SLOT_LOCKS = Path(os.environ.get("GAMEENV_SLOT_LOCKS", "/tmp/game2player-rollout-slots"))
MAX_SLOTS = 40
# Finished environment verdicts and completed collections are not restarted.
SETTLED = {"done", "unsuitable", "repair_failed", "rejected"}
# Games that need attention or an agent reset before their requested stages finish.
INCOMPLETE = {"error", "paused", "waiting_agent", "blocked", "needs_environment", "review_pending"}
# An agent whose CLI fails this many games in a row rests this long.
AGENT_FAILURES = 3
AGENT_REST_S = 30 * 60
# Games only collecting episodes hold no agent; at most this many run beyond the
# agents' jobs.
COLLECTING_EXTRA = 8


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def _read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _timed(stage: dict, call):
    """Run one call and add its wall time to the stage, even when it fails."""
    started = time.monotonic()
    try:
        return call()
    finally:
        stage["seconds"] = round(stage.get("seconds", 0) + time.monotonic() - started, 1)


def _add_tokens(usages) -> dict[str, int] | None:
    totals: dict[str, int] = {}
    for usage in usages:
        for key, value in (usage or {}).items():
            totals[key] = totals.get(key, 0) + value
    return totals or None


def _stage_tokens(stage: dict) -> dict[str, int] | None:
    """Tokens read back from the agents' own logs, so resumed runs count in full."""
    run_dirs = [stage.get("run_dir"), *stage.get("repair_run_dirs", [])]
    return _add_tokens(agent_usage(Path(run_dir))
                       for run_dir in run_dirs if run_dir)


def _cost(stages) -> dict:
    stages = list(stages)
    return {"seconds": round(sum(stage.get("seconds", 0) for stage in stages), 1),
            "tokens": _add_tokens(stage.get("tokens") for stage in stages)}


def _candidates(report: dict, games: list[dict]) -> dict[str, dict]:
    by_url = {item["source_url"].rstrip("/"): item for item in report.get("candidates", [])}
    missing = [game["source_url"] for game in games
               if game["source_url"].rstrip("/") not in by_url]
    if missing:
        raise ValueError(f"Scout did not report the specified URLs: {missing}")
    return {game["game_id"]: by_url[game["source_url"].rstrip("/")] for game in games}


def _scout_report(directory: Path, game_id: str) -> Path:
    return directory / "scout" / f"{game_id}.json"


def _scout_brief(game: dict) -> str:
    brief = (
        "Inspect only the specified game below; do not search for substitutes or "
        "additional games. Return exactly one candidate for it, preserving its source_url "
        "exactly so the runner can associate your report with the game. This is a "
        "game integration request. Assess the existing "
        "browser/SWF version, offline solo play, natural short tasks and state access. Use "
        "the supplied alternative asset/source links when a portal blocks automated HTTP "
        "access. Never claim to have played a game merely because its page loads.\n\n"
    )
    if game.get("task_spec"):
        brief += (
            "This game is already selected for a published benchmark. Assess whether "
            "the specified tasks can be reconstructed, not whether to replace the game "
            "with a more complex one. Read its task_spec file. "
        )
        if not game["source_url"].startswith(("https://", "http://")):
            brief += (
                "The source is a local directory relative to this repository. Inspect "
                "those existing files without searching for another game. "
            )
        brief += "\n\n"
    return brief + json.dumps([game], ensure_ascii=False, indent=2)


def _campaign_dir(root: Path, campaign: str) -> Path:
    return root / ".cache" / "game-pipeline" / campaign


def _prepare_games(plan: dict, root: Path, used_ids: set[str]) -> dict:
    """Resolve one Scout search, then give every game a stable local name."""
    plan = dict(plan)
    saved = _read(_campaign_dir(root, plan["campaign"]) / "plan.json")
    prompt = plan.get("scout_prompt")
    if prompt:
        if plan.get("games"):
            raise ValueError("use either scout_prompt or games in a campaign")
        if saved.get("scout_prompt"):
            if saved["scout_prompt"] != prompt:
                raise ValueError("use a new campaign name for a different scout_prompt")
            games = saved["games"]
        else:
            scout = Scout(f"{plan['campaign']}-search", root=root,
                          agent=load_agent(plan["agent"]),
                          turn_timeout_s=plan.get("options", {}).get("turn_timeout_minutes", 25) * 60)
            report = (scout.state.reports[-1] if scout.state.reports
                      else scout.run(prompt, discover=True))
            print(f"[{plan['campaign']}] Scout: {report['summary']}", flush=True)
            games = report["candidates"]
    else:
        if not plan.get("games"):
            raise ValueError("a campaign needs scout_prompt or games")
        games = plan["games"]
    previous = {game["source_url"]: game for game in saved.get("games", [])}
    used_ids.update(game["game_id"] for game in games if game.get("game_id"))
    used_ids.update(game["game_id"] for game in previous.values() if game.get("game_id"))
    number = max((int(match.group(1)) for game_id in used_ids
                  if (match := re.match(r"^(\d+)_", game_id))), default=0) + 1
    prepared = []
    for source in games:
        game = dict(source)
        url = game["source_url"]
        old = previous.get(url, {})
        game.setdefault("title", old.get("title") or
                        urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or "Game")
        if not game.get("game_id"):
            game["game_id"] = old.get("game_id")
            if not game["game_id"]:
                slug = re.sub(r"[^a-z0-9]+", "-", game["title"].lower()).strip("-") or "game"
                game["game_id"] = f"{number}_{slug}"
                number += 1
        used_ids.add(game["game_id"])
        prepared.append(game)
    plan["games"] = prepared
    return plan


def _policy_path(root: Path, game_id: str) -> Path:
    return root / "harnesses" / "policy" / "policies" / f"g{game_id.replace('-', '_')}.py"


def _built(row: dict, root: Path, game_id: str) -> bool:
    """A game this campaign built, or one built before it that the plan lists."""
    if row.get("status") == "rejected":
        return False
    status = (row.get("builder") or {}).get("status")
    if status is not None:
        return status == "passed"
    return (root / "catalog" / "games" / f"{game_id}.yaml").is_file()


def _completed(root: Path, game_id: str) -> list[str] | None:
    """The tasks the game's policy completes; None without a policy or its list."""
    path = _policy_path(root, game_id)
    if not path.is_file() or not path.with_suffix(".tasks.json").is_file():
        return None
    return list(json.loads(path.with_suffix(".tasks.json").read_text()))


def _tag(text: str) -> str:
    return "".join(character if character.isalnum() or character in "-_.@" else "-"
                   for character in text)


def _served_endpoints(directory: Path, model: str) -> Path:
    """Where the parent writes the endpoints of the campaign's servers of a model."""
    return directory / "served" / f"{_tag(model)}.json"


def _player_thinking(config: dict, root: Path) -> str:
    """Stage override, then episode plan, then the model profile's default."""
    level = config.get("thinking")
    if level is None:
        plan = yaml.safe_load((root / config["plan"]).read_text()) or {}
        level = plan.get("thinking")
    return load_model(config["model"]).resolve_thinking(level)


def _stop(running: dict[str, subprocess.Popen]) -> None:
    """Let child processes stop their agents, then terminate whatever remains."""
    deadline = time.monotonic() + STOP_GRACE_S
    for process in running.values():
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.terminate()
    for process in running.values():
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()


def _spawn(command: list[str], log_path: Path, *, cwd: Path, env=None,
           group: bool = False) -> subprocess.Popen:
    """Start a child that logs to `log_path`; with `group`, in a process group of its
    own, which `_stop_group` ends whole."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log:
        log.write(f"=== {datetime.now().isoformat(timespec='seconds')} {' '.join(command)}\n")
        log.flush()
        return subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=group)


def _descendants(pid: int) -> list[int]:
    """Every live process below `pid`, read from /proc: episode workers start sessions
    of their own, so a process group does not reach them."""
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
    found, todo = [], [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            found.append(child)
            todo.append(child)
    return found


def _stop_group(process: subprocess.Popen) -> None:
    """End a child started with `group` and everything below it: an interrupt first,
    so it can close its episodes, then termination. Further interrupts and the parent's
    terminate are ignored meanwhile, so they cannot cut the sequence short and leave
    the rollout's episodes and browsers running."""
    previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig, wait in ((signal.SIGINT, STOP_GRACE_S), (signal.SIGTERM, 30.0),
                          (signal.SIGKILL, 10.0)):
            targets = _descendants(process.pid)
            for target in [process.pid, *targets]:
                try:
                    os.kill(target, sig)
                except ProcessLookupError:
                    pass
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                process.poll()
                if process.returncode is not None and not _descendants(process.pid) \
                        and not any(Path(f"/proc/{target}").exists() for target in targets):
                    return
                time.sleep(0.2)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _rollout(arguments: list[str], record: dict, *, root: Path, log_path: Path) -> int:
    """Run tools.rollout with the checkout's own code and catalog, and record its exit
    status, wall time and model tokens."""
    command = [sys.executable, "-m", "tools.rollout", "run", *arguments]
    out = Path(arguments[arguments.index("--out") + 1])
    # Its own process group: an interrupt reaches this process, which then ends the
    # rollout with every episode and browser it started.
    process = _spawn(command, log_path, cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
                     group=True)
    try:
        record["exit"] = _timed(record, process.wait)
    except BaseException:
        _stop_group(process)
        raise
    finally:
        record["out"] = str(out)
        record["tokens"] = episode_usage(out)
    return record["exit"]


_PLAN_LIMIT = re.compile(r"\[plan-limit backend=(\w[\w-]*) resets_at=(\d+)\]")


def _model_limit(text: str) -> tuple[str, float] | None:
    """The coding backend whose plan a player model's limit belongs to, and when it
    resets, from the marker `ModelUsageLimitError` puts in its message; None when the
    failure is not a plan limit."""
    match = _PLAN_LIMIT.search(text)
    return (match[1], float(match[2])) if match else None


class RolloutPaused(RuntimeError):
    """An execution or recording failure needs attention before a game continues."""


class GameRun:
    """One game through the stages it is given, keeping its progress row current."""

    def __init__(self, plan: dict, game_id: str, *, root: Path, slot: int | None = None,
                 agent: str | None = None):
        self.plan = plan
        self.game_id = game_id
        self.slot = slot
        self.root = root.resolve()
        self.profile = load_agent(agent or plan["agent"])
        self.campaign = plan["campaign"]
        self.options = plan.get("options", {})
        self.timeout = self.options.get("turn_timeout_minutes", 25) * 60
        self.directory = _campaign_dir(self.root, self.campaign)
        self.game = next(item for item in plan["games"] if item["game_id"] == game_id)
        self.row_path = self.directory / "games" / f"{game_id}.json"
        self.row = _read(self.row_path)
        for key in ("error", "agent_unavailable", "agent_failed"):
            self.row.pop(key, None)
        # The agent whose conversations this game's stages are in.
        self.row["agent"] = self.profile.name

    def save(self, status: str | None = None) -> None:
        if status is not None:
            self.row["status"] = status
        agents = [self.row[key] for key in ("scout", "builder", "policy") if key in self.row]
        agents += [item["repair"] for item in (self.row.get("vlm_check") or {}).get("rounds", [])
                   if "repair" in item]
        for stage in agents:
            stage["tokens"] = _stage_tokens(stage)
        self.row["cost"] = _cost(agents)
        _write(self.row_path, self.row)

    def builder(self, *, brief: str, run_id: str, **extra) -> Builder:
        return Builder(self.game_id, root=self.root, agent=self.profile, brief=brief,
                       task_spec=(self.root / self.game["task_spec"]
                                  if self.game.get("task_spec") else None),
                       run_id=run_id, full_access=True,
                       max_revisions=self.options.get("builder_revisions", 3),
                       turn_timeout_s=self.timeout, **extra)

    def rollout(self, name: str, config: dict, out: Path, record: dict,
                tasks: list[str] | None = None) -> int:
        level = None
        arguments = [config["plan"], "--games", self.game_id, "--out", str(out)]
        if tasks:
            arguments += ["--tasks", ",".join(tasks)]
        if config.get("model") == "policy":
            arguments += ["--model", "policy"]
        else:
            arguments += ["--model", config["model"]]
            if load_model(config["model"]).serve is not None:
                endpoints = _read(_served_endpoints(self.directory, config["model"])).get("endpoints")
                if not endpoints:
                    raise ValueError(f"{config['model']} is served locally: run the campaign with --gpus")
                # The game's episodes take the campaign's servers in turn.
                arguments += ["--endpoints", ",".join(endpoints)]
            level = _player_thinking(config, self.root)
            arguments += ["--thinking", level]
            arguments += ["--workers", str(self.options.get("vlm_workers", 2))]
        if self.slot is not None:
            base = ROLLOUT_PORT_BASE + self.slot * ROLLOUT_PORT_BLOCK
            arguments += ["--port-base", str(base),
                          "--control-port-base", str(base + ROLLOUT_PORT_BLOCK // 2)]
        record.update(model=config["model"], thinking=level)
        log_path = self.directory / "logs" / self.game_id / f"{name}.log"
        print(f"[{self.game_id}] {name}: rollout, log {log_path}", flush=True)
        code = _rollout(arguments, record, root=self.root, log_path=log_path)
        status = _read(out / "_scheduler" / "status.json")
        failures = [job for job in status.get("exhausted_jobs") or []
                    if job.get("game_id") == self.game_id]
        if (name.startswith("vlm_check_round") and failures
                and all(job.get("failure_kind") == "environment" for job in failures)):
            reported = {(item["task"], item["seed"]) for item in system_reports(out)
                        if item["game_id"] == self.game_id}
            if all((job["task_id"], job["seed"]) in reported for job in failures):
                return code  # The existing Builder review handles these reports below.
        if failures or self.game_id in (status.get("paused_games") or {}) or code:
            details = "; ".join(f"{job['task_id']}: {job.get('reason', '')}" for job in failures)
            details = details or str((status.get("paused_games") or {}).get(self.game_id) or f"exit {code}")
            error = ModelRequestError if any(job.get("failure_kind") == "model" for job in failures) else RolloutPaused
            cause = "model requests failed" if error is ModelRequestError else "execution failed"
            raise error(f"{name} {cause} for {self.game_id}: {details}; "
                        f"see {out / '_scheduler' / 'failed_attempts'} and {log_path}. "
                        "The game is paused; resume after fixing the failure.")
        return code

    def run(self, stages: tuple[str, ...]) -> dict:
        try:
            if "scout" in stages:
                self.scout()
            if "builder" in stages and self.row.get("status") != "rejected":
                self.build()
            if "vlm_check" in stages and _built(self.row, self.root, self.game_id):
                self.vlm_check()
                if (self.row.get("vlm_check") or {}).get("status") != "paused":
                    remove_debug_media(self.directory / "vlm_check" / self.game_id)
            if "policy" in stages and self.can_author_policy():
                self.author_policy()
            if "collect" in stages and self.checked():
                policy_status = (self.row.get("policy") or {}).get("status")
                if policy_status in {"blocked", "needs_environment"}:
                    self.save(policy_status)
                else:
                    self.collect()
        except AgentUsageLimitError as exc:
            # Not the game's fault: it waits until the agent's plan limit resets.
            self.wait_for_limit(self.profile.backend, exc.resets_at, exc)
        except (ModelRequestError, RolloutPaused) as exc:
            # A player model at its plan limit pauses the rollout the same way; its
            # episodes are played again once the limit resets.
            limit = _model_limit(str(exc))
            if limit is not None:
                self.wait_for_limit(*limit, exc)
                print(f"[{self.game_id}] finished: {self.row.get('status')}", flush=True)
                return self.row
            self.row["error"] = str(exc)
            if self.row.get("status") == "checking":
                self.row["vlm_check"]["status"] = "paused"
            self.save("paused")
        except CodingProcessError as exc:
            self.row["error"] = str(exc)
            self.row["agent_failed"] = self.profile.name
            traceback.print_exc()
            self.save("error")
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            self.row["error"] = str(exc)
            traceback.print_exc()
            self.save("error")
        except BaseException:
            self.save()  # an interrupted game stays resumable; keep the time it spent
            raise
        print(f"[{self.game_id}] finished: {self.row.get('status')}", flush=True)
        return self.row

    def wait_for_limit(self, backend: str, resets_at: float, exc: BaseException) -> None:
        if self.row.get("status") == "checking":
            # Not checked yet: a later --from policy or collect must not take it as clean.
            self.row.setdefault("vlm_check", {})["status"] = "paused"
        self.row["agent_unavailable"] = {"agent": self.profile.name, "backend": backend,
                                         "until": resets_at, "reason": str(exc)[:500]}
        print(f"[{self.game_id}] the {backend} plan reached its usage limit; it resets at "
              f"{datetime.fromtimestamp(resets_at):%Y-%m-%d %H:%M}", flush=True)
        self.save("waiting_agent")

    def checked(self) -> bool:
        """Built, with no player report left awaiting review."""
        status = (self.row.get("vlm_check") or {}).get("status")
        return _built(self.row, self.root, self.game_id) and status in {None, "clean"}

    def can_author_policy(self) -> bool:
        return self.checked() or (
            _built(self.row, self.root, self.game_id)
            and (self.row.get("vlm_check") or {}).get("status") == "review_pending"
        )

    def scout(self) -> None:
        """Inspect this game alone, so its research shares a session with no other game."""
        report_path = _scout_report(self.directory, self.game_id)
        stage = self.row.setdefault("scout", {})
        fresh = not report_path.exists()
        if fresh:
            self.save("scouting")
            print(f"[{self.game_id}] Scout", flush=True)
            scout = Scout(f"{self.campaign}-{self.game_id}", root=self.root, agent=self.profile,
                          turn_timeout_s=self.timeout)
            stage["run_dir"] = str(scout.run_dir)
            report = _timed(stage, lambda: scout.run(_scout_brief(self.game)))
            _write(report_path, report)
        candidate = _candidates(_read(report_path), [self.game])[self.game_id]
        stage.update(decision=candidate["decision"], reason=candidate["decision_reason"])
        if candidate["decision"] == "reject":
            print(f"[{self.game_id}] Scout rejected: {candidate['decision_reason']}", flush=True)
            self.save("rejected")
        else:
            self.save("scouted" if fresh else None)

    def build(self) -> None:
        report_path = _scout_report(self.directory, self.game_id)
        if not report_path.exists():
            raise ValueError(f"{self.game_id} has no Scout report; run it from the scout stage")
        candidate = _candidates(_read(report_path), [self.game])[self.game_id]
        self.save("building")
        print(f"[{self.game_id}] Builder", flush=True)
        builder = self.builder(
            run_id=self.campaign, source_url=self.game["source_url"], title=self.game["title"],
            engine=self.game.get("engine", "unknown"),
            brief="Build this existing game, preserving its gameplay.\n"
                  + self.game.get("notes", "") + "\nScout findings:\n"
                  + json.dumps(candidate, ensure_ascii=False))
        stage = self.row.setdefault("builder", {})
        stage["run_dir"] = str(builder.run_dir)
        built = _timed(stage, lambda: builder.run())
        stage.update(status=built.status, revisions=built.revisions, turns=built.turns)
        self.save(built.status)

    def vlm_check(self) -> None:
        """Play once; the Builder's targeted checks resolve suspected problems."""
        config = self.plan["vlm_check"]
        stage = self.row.setdefault("vlm_check", {})
        # Finished checks resume from their recorded verdict, after their debug
        # media has been discarded. An interrupted check still reuses episodes.
        if stage.get("status") in {"clean", "repair_failed"}:
            self.save("checked" if stage["status"] == "clean" else stage["status"])
            return
        stage.pop("status", None)
        rounds = stage.setdefault("rounds", [])
        base = self.directory / "vlm_check" / self.game_id / _tag(f"{config['model']}-{config.get('thinking') or 'plan'}")
        self.save("checking")
        if not rounds:
            rounds.append({})
        while True:
            number = len(rounds)
            record = rounds[-1]
            out = Path((record.get("rollout") or {}).get("out") or base / f"round{number}")
            # A stopped Builder review resumes without replaying the player's episode.
            if "reported" not in record:
                tasks = self.game.get("vlm_check_tasks")
                record["tasks"] = record.get("tasks") or tasks or "all"
                tasks = None if record["tasks"] == "all" else record["tasks"]
                play = record.setdefault("rollout", {})
                self.rollout(f"vlm_check_round{number}", config, out, play, tasks)
            reviewed = {episode for earlier in rounds[:-1]
                        for episode in earlier.get("report_episodes", [])}
            requests = [request for request in builder_requests(out, min_episodes=1, exclude=reviewed)
                        if request["game_id"] == self.game_id]
            if record.get("reported") and not requests:
                raise ValueError(f"saved reports have no episode records or system reports under {out}")
            record["reported"] = {str(item.get("task")): str(item.get("reason") or "")
                                  for request in requests for item in request["reports"]}
            record["report_episodes"] = [item["episode"] for request in requests for item in request["reports"]]
            self.save()
            if not requests:
                stage["status"] = "clean"
                self.save("checked")
                return
            request = requests[0]
            record["system_failure"] = any(item.get("source") == "system" for item in request["reports"])
            prior_repairs = sum(bool(item.get("system_failure") and item.get("repair"))
                                for item in rounds[:-1])
            if record["system_failure"] and prior_repairs >= self.options.get("repair_rounds", 2):
                stage["status"] = "paused"
                self.row["error"] = "Environment interface repair round limit reached"
                self.save("paused")
                return
            print(f"[{self.game_id}] checking reports: {sorted(record['reported'])}", flush=True)
            repair = self.builder(brief=request["builder_brief"], player_reports=request["reports"],
                                  run_id=f"reported-{self.campaign}-r{number}")
            fix = record.setdefault("repair", {})
            fix["run_dir"] = str(repair.run_dir)
            repaired = _timed(fix, lambda: repair.run())
            fix["status"] = repaired.status
            self.save()
            if repaired.status != "passed":
                stage["status"] = "repair_failed"
                self.save("repair_failed")
                return
            checks = repaired.history[-1].get("report_checks")
            if not checks:
                raise ValueError("Builder review has no per-report conclusions; resume its EVALUATE phase")
            fix["report_checks"] = checks
            unresolved = any(c["status"] == "unresolved" for c in checks)
            if record["system_failure"]:
                if unresolved:
                    stage["status"] = "paused"
                    self.row["error"] = "Environment interface report remains unresolved"
                    self.save("paused")
                    return
                # Reuse this output root: its seed namespace and completed episodes
                # stay unchanged. Only failed and unattempted slots run again.
                rounds.append({"tasks": record["tasks"], "rollout": {"out": str(out)}})
                self.save()
                continue
            stage["status"] = "review_pending" if unresolved else "clean"
            self.save("review_pending" if unresolved else "checked")
            return

    def author_policy(self) -> None:
        """Write the policy, forwarding the environment requests it makes."""
        self.save("authoring_policy")
        stage = self.row.setdefault("policy", {})
        for key in ("reason", "repair_status"):
            stage.pop(key, None)
        handoff = None
        rounds = (self.row.get("vlm_check") or {}).get("rounds", [])
        pending_reports = [check for check in (rounds[-1].get("repair") or {}).get("report_checks", [])
                           if check["status"] == "unresolved"] if rounds else []
        while True:
            author = PolicyAuthor(
                self.game_id, root=self.root, agent=self.profile, experiment=self.campaign,
                max_revisions=self.options.get("policy_revisions", 50),
                turn_timeout_s=self.timeout, full_access=True, environment_handoff=handoff,
                player_reports=pending_reports,
            )
            stage["run_dir"] = str(author.run_dir)
            state = _timed(stage, lambda: author.run())
            stage.update(status=state.status, state_requests=state.state_requests)
            self.save()
            if state.status != "needs_environment":
                break
            if state.state_requests > self.options.get("repair_rounds", 2):
                stage["reason"] = "Environment repair round limit reached"
                break
            brief, run_id = _load_build_request(author.environment_request_path, self.game_id)
            repair = self.builder(brief=brief, run_id=run_id)
            repairs = stage.setdefault("repair_run_dirs", [])
            if str(repair.run_dir) not in repairs:
                repairs.append(str(repair.run_dir))
            print(f"[{self.game_id}] environment repair {state.state_requests}", flush=True)
            repaired = _timed(stage, lambda: repair.run())
            self.save()
            if repaired.status != "passed":
                stage["repair_status"] = repaired.status
                break
            handoff = repair.state_path
        # This verdict describes the environment. A finished policy attempt can
        # solve all, some or none of its tasks; its own verdict stays in `policy`.
        if stage["status"] in {"passed", "partial", "unsuitable"}:
            pending = (self.row.get("vlm_check") or {}).get("status") == "review_pending"
            self.save("review_pending" if pending else "passed")
        else:
            self.save(stage["status"])

    def collect(self) -> None:
        """Policy episodes on the tasks the policy completes, model episodes on all."""
        record = self.row.setdefault("collect", {})
        if self.game_id in set(self.plan.get("held_out") or []):
            record["skipped"] = "held out"
            self.save("done")
            return
        self.save("collecting")
        out = self.root / "data" / "trajs" / "collect" / self.campaign / self.game_id
        if _policy_path(self.root, self.game_id).is_file() and _completed(self.root, self.game_id) != []:
            config = {**self.plan["policy_collect"], "model": "policy"}
            self.rollout("collect_policy", config, out / "policy", record.setdefault("policy", {}))
        config = self.plan["vlm_collect"]
        tag = _tag(f"{config['model']}-{config.get('thinking') or 'plan'}")
        self.rollout("collect_vlm", config, out / tag, record.setdefault("vlm", {}))
        self.save("done")


def _check_unique(games: list[dict]) -> None:
    """Two processes on one game would overwrite each other's environment."""
    for key in ("game_id", "source_url"):
        values = [str(game[key]).rstrip("/") for game in games]
        repeated = sorted({value for value in values if values.count(value) > 1})
        if repeated:
            raise ValueError(f"plan repeats {key}: {repeated}")


def _check_plan(plan: dict, stages: tuple[str, ...]) -> None:
    needs = {"vlm_check": ("plan", "model"), "policy_collect": ("plan",), "vlm_collect": ("plan", "model")}
    wanted = (["vlm_check"] if "vlm_check" in stages else []) + \
             (["policy_collect", "vlm_collect"] if "collect" in stages else [])
    missing = [f"{key}.{field}" for key in wanted for field in needs[key]
               if not (plan.get(key) or {}).get(field)]
    if missing:
        raise ValueError(f"the campaign plan needs {', '.join(missing)}")
    if plan.get("options", {}).get("vlm_workers", 2) > ROLLOUT_PORT_BLOCK // 2:
        raise ValueError(f"vlm_workers is at most {ROLLOUT_PORT_BLOCK // 2}, the ports one "
                         "game's rollout has")
    held_out = set(plan.get("held_out") or [])
    unknown = sorted(held_out - {game["game_id"] for game in plan["games"]})
    if unknown:
        raise ValueError(f"held_out names games the plan does not list: {unknown}")


def _assemble(progress: dict, directory: Path, games: list[dict]) -> None:
    progress["games"] = {game["game_id"]: _read(directory / "games" / f"{game['game_id']}.json")
                         for game in games}
    # Seconds add up every agent's wall time, so with several jobs they exceed the
    # campaign's own duration.
    progress["cost"] = _cost(row.get("cost", {}) for row in progress["games"].values())
    _write(directory / "progress.json", progress)


def _claim_slot(first: int = 0):
    """The first slot from `first` that no other game on the machine holds and whose
    ports are free, with the open lock file that holds it until closed."""
    SLOT_LOCKS.mkdir(parents=True, exist_ok=True)
    for slot in range(first, first + MAX_SLOTS):
        handle = (SLOT_LOCKS / f"slot-{slot}.lock").open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            continue
        base = ROLLOUT_PORT_BASE + slot * ROLLOUT_PORT_BLOCK
        if port_free(base) and port_free(base + ROLLOUT_PORT_BLOCK // 2):
            return slot, handle
        handle.close()
    raise RuntimeError(f"all {MAX_SLOTS} rollout port blocks from slot {first} are in use")


@dataclass
class PoolAgent:
    """One coding agent of the pool and the games it may take at a time."""

    name: str
    jobs: int
    backend: str
    running: int = 0
    # No new games before this time: a usage limit, or a rest after repeated failures.
    until: float = 0.0
    failures: int = 0

    def available(self, now: float) -> bool:
        return now >= self.until

    def free(self, now: float) -> bool:
        return self.running < self.jobs and self.available(now)


def parse_agents(text: str) -> list[PoolAgent]:
    """`claude=2,codex=2`: agents in order of preference, with their jobs."""
    agents = []
    for item in text.split(","):
        name, _, jobs = item.strip().partition("=")
        if not name:
            continue
        count = int(jobs) if jobs else 1
        if count < 1:
            raise ValueError(f"{name}: an agent takes at least one game")
        agents.append(PoolAgent(name=name, jobs=count, backend=load_agent(name).backend))
    if not agents:
        raise ValueError("--agents names no agent")
    if len({agent.backend for agent in agents}) != len(agents):
        # A started game keeps its CLI's conversation, so each CLI is in the pool once.
        raise ValueError("--agents lists one agent per backend")
    return agents


@dataclass
class PoolGame:
    directory: Path
    game_id: str
    plan_agent: str
    # Agents whose CLI failed this game, before it started, in this run.
    failed_on: set[str] = field(default_factory=set)
    # Not started again before this time: its player model is at a plan limit.
    not_before: float = 0.0


_BACKEND_OF: dict[str, str] = {}


def _backend_of(name: str) -> str:
    if name not in _BACKEND_OF:
        _BACKEND_OF[name] = load_agent(name).backend
    return _BACKEND_OF[name]


def _held_backend(row: dict, plan_agent: str) -> str | None:
    """The backend whose conversations a started game is in, or None for a game whose
    Builder has not started: a Scout's session is the game's own and not resumed."""
    if not any((row.get(key) or {}).get("run_dir") for key in ("builder", "policy")):
        return None
    return _backend_of(row.get("agent") or plan_agent)


def _choose_agent(game: PoolGame, row: dict, agents: list[PoolAgent], now: float) -> PoolAgent | None:
    """The agent that takes the game now, or None to wait: a started game waits for
    its own agent, and a new one takes the first agent with room that has not failed it."""
    held = _held_backend(row, game.plan_agent)
    if held is not None:
        own = next((agent for agent in agents if agent.backend == held), None)
        if own is None:
            raise ValueError(f"{game.game_id} was started on {row.get('agent') or game.plan_agent}, "
                             "which is not in --agents")
        return own if own.free(now) else None
    return next((agent for agent in agents
                 if agent.free(now) and agent.name not in game.failed_on), None)


def _run_pool(games: list[PoolGame], arguments: list[str], *, agents: list[PoolAgent] | None,
              campaigns: dict[Path, tuple[dict, list[dict]]], jobs: int = 1,
              slot_base: int = 0) -> None:
    """Run one child per game. With `agents`, each child gets the agent that
    `_choose_agent` picks and at most that agent's jobs run together; without, the
    plan's own agent and at most `jobs` games at a time."""
    queue = list(games)
    running: dict[str, tuple[PoolGame, subprocess.Popen, PoolAgent | None]] = {}
    locks: dict[str, object] = {}

    def key(game: PoolGame) -> str:
        return f"{game.directory.name}/{game.game_id}"

    def row_of(game: PoolGame) -> dict:
        return _read(game.directory / "games" / f"{game.game_id}.json")

    def launch(game: PoolGame, agent: PoolAgent | None, claimed) -> None:
        slot, locks[key(game)] = claimed
        command = [sys.executable, "-m", "tools.game_pipeline", str(game.directory / "plan.json"),
                   "--game", game.game_id, "--slot", str(slot), *arguments]
        if agent is not None:
            command += ["--agent", agent.name]
            agent.running += 1
        log_path = game.directory / "logs" / f"{game.game_id}.log"
        # In a pool a Codex session at its usage limit stops, so its game waits for
        # the reset here instead of holding a job while it sleeps.
        env = {**os.environ, "GAMEENV_CODEX_WAIT_FOR_LIMIT": "0"} if agents is not None else None
        running[key(game)] = (game, _spawn(command, log_path, cwd=CODE_ROOT, env=env), agent)
        named = f" on {agent.name}" if agent is not None else ""
        print(f"[{game.game_id}] started{named}, log {log_path}", flush=True)

    def recount() -> None:
        """Each agent's jobs in use: its games that are not just collecting episodes."""
        for agent in agents or []:
            agent.running = 0
        for game, _, agent in running.values():
            if agent is not None and row_of(game).get("status") != "collecting":
                agent.running += 1

    capacity = (sum(agent.jobs for agent in agents) + COLLECTING_EXTRA) if agents else jobs
    try:
        while queue or running:
            if agents is not None:
                recount()
            now = time.time()
            for game in list(queue):
                if len(running) >= capacity:
                    break
                if agents is None:
                    agent = None
                else:
                    if now < game.not_before:
                        continue
                    agent = _choose_agent(game, row_of(game), agents, now)
                    if agent is None:
                        continue
                try:
                    claimed = _claim_slot(slot_base)
                except RuntimeError as exc:
                    # Other runs hold every port block: try again on the next poll.
                    print(f"[pool] {exc}; waiting for one to free", flush=True)
                    break
                queue.remove(game)
                launch(game, agent, claimed)
            time.sleep(POLL_S)
            for name, (game, process, agent) in list(running.items()):
                if process.poll() is None:
                    continue
                del running[name]
                locks.pop(name).close()
                row_path = game.directory / "games" / f"{game.game_id}.json"
                row = _read(row_path)
                if process.returncode != 0 and row.get("status") not in INCOMPLETE:
                    row["status"] = "error"
                    row["error"] = (f"game process exited with {process.returncode}; "
                                    f"see logs/{game.game_id}.log")
                    _write(row_path, row)
                print(f"[{game.game_id}] finished: {row.get('status')}", flush=True)
                if agent is None:
                    continue
                agent.running -= 1
                if row.get("status") == "waiting_agent":
                    limit = row["agent_unavailable"]
                    until = float(limit["until"])
                    backend = limit.get("backend") or agent.backend
                    for limited in agents:
                        if limited.backend == backend:
                            limited.until = max(limited.until, until)
                    if backend != agent.backend:
                        # A player model's limit: the game's agent can still work.
                        game.not_before = until
                    print(f"[pool] the {backend} plan rests until "
                          f"{datetime.fromtimestamp(until):%H:%M}; {game.game_id} goes back "
                          "to the queue", flush=True)
                    queue.insert(0, game)
                elif row.get("agent_failed"):
                    agent.failures += 1
                    if agent.failures >= AGENT_FAILURES:
                        agent.until, agent.failures = time.time() + AGENT_REST_S, 0
                        print(f"[pool] {agent.name} failed {AGENT_FAILURES} games in a row; "
                              f"it rests for {AGENT_REST_S // 60} min", flush=True)
                    # A game no agent had started can go to another one; a started
                    # game stays with its agent and is left for a rerun.
                    game.failed_on.add(agent.name)
                    if _held_backend(row, game.plan_agent) is None and \
                            any(other.name not in game.failed_on for other in agents):
                        print(f"[pool] {game.game_id}: {agent.name} failed; another agent "
                              "takes it", flush=True)
                        queue.insert(0, game)
                else:
                    agent.failures = 0
            for directory, (progress, plan_games) in campaigns.items():
                _assemble(progress, directory, plan_games)
            if agents is not None and queue and not running:
                now = time.time()
                waits = [game.not_before for game in queue if game.not_before > now]
                waits += [agent.until for agent in agents if not agent.available(now)]
                if waits and not any(agent.free(now) for agent in agents) or \
                        waits and all(game.not_before > now for game in queue):
                    wake = min(waits)
                    print(f"[pool] nothing can start until a limit resets; waiting until "
                          f"{datetime.fromtimestamp(wake):%Y-%m-%d %H:%M}", flush=True)
                    time.sleep(max(0.0, min(wake - now, 600)))
    except BaseException:
        _stop({name: process for name, (_, process, _) in running.items()})
        raise
    finally:
        for lock in locks.values():
            lock.close()


def _run_games(work: list[str], arguments: list[str], *, directory: Path, jobs: int,
               progress: dict, games: list[dict], slot_base: int = 0) -> None:
    """Run one child per game of one campaign on its own agent, at most `jobs` at a
    time, each in a port block of its own from `slot_base`."""
    _run_pool([PoolGame(directory, game_id, "") for game_id in work], arguments, agents=None,
              campaigns={directory: (progress, games)}, jobs=jobs, slot_base=slot_base)


def _serve(plan: dict, stages: tuple[str, ...], work: list[str], *, directory: Path,
           root: Path, gpus: str | None, wait_idle: bool, vllm: str | None,
           others: list[tuple[Path, list[str]]] = ()) -> list:
    """Start the campaign's model servers, if a stage plays a local model, and point
    each game at them; return the servers for the caller to stop. `others` are the
    directories and games of further campaigns, with the same stage models, that use
    these servers too."""
    work = [*work, *(game_id for _, games in others for game_id in games)]
    configs = ([plan["vlm_check"]] if "vlm_check" in stages else []) + \
              ([plan["vlm_collect"]] if "collect" in stages else [])
    local = {config["model"] for config in configs if load_model(config["model"]).serve is not None}
    if not local or not work:
        return []
    if len(local) > 1:
        raise ValueError(f"one set of GPUs serves one local model, not {sorted(local)}; "
                         "use an API model for the other stage")
    if not gpus:
        raise ValueError(f"{next(iter(local))} is served locally: pass --gpus")
    model = load_model(next(iter(local)))
    indices = [int(item) for item in gpus.split(",") if item.strip()]
    busy = [SERVER_PORT_BASE + gpu for gpu in indices if not port_free(SERVER_PORT_BASE + gpu)]
    if busy:
        raise ValueError(f"server ports in use: {busy}")
    require_idle_gpus(indices, wait=wait_idle)
    states = gpu_states()
    thinking = any(_player_thinking(config, root) != "off" for config in configs
                   if config["model"] in local)
    servers = []
    try:
        for gpu in indices:
            servers.append(start_server(model, gpu=gpu, device=states[gpu]["uuid"],
                                        port=SERVER_PORT_BASE + gpu,
                                        vllm=vllm or os.environ.get("GAMEENV_VLLM") or "vllm",
                                        weights=None, thinking=thinking,
                                        log_dir=directory / "served" / "logs"))
    except BaseException:
        for server in servers:
            server.stop()
        raise
    # Every game plays on all of them, so whichever games reach a model stage together
    # load the GPUs evenly.
    endpoints = {"endpoints": [server.endpoint for server in servers]}
    for campaign in [directory, *(other for other, _ in others)]:
        _write(_served_endpoints(campaign, model.model_name), endpoints)
    return servers


def _work(directory: Path, games: list[dict], stages: tuple[str, ...], root: Path) -> list[str]:
    """The games still to run: not settled, and built when the run starts after the
    Builder."""
    rows = {game["game_id"]: _read(directory / "games" / f"{game['game_id']}.json")
            for game in games}
    return [game_id for game_id, row in rows.items()
            if row.get("status") not in SETTLED
            and ("scout" in stages or "builder" in stages or _built(row, root, game_id))]


def run_campaigns(plans: list[dict], *, root: Path, start: str = "scout", until: str = "collect",
                  jobs: int = 1, agents: list[PoolAgent] | None = None, gpus: str | None = None,
                  wait_idle: bool = False, vllm: str | None = None,
                  slot_base: int = 0) -> dict[str, dict]:
    """Run campaigns together: with `agents` as one pool, else one campaign on its own
    agent with `jobs` games at a time. Returns each campaign's progress."""
    root = root.resolve()
    stages = STAGES[STAGES.index(start):STAGES.index(until) + 1]
    if agents is None and len(plans) != 1:
        raise ValueError("several campaigns run together need --agents")
    used_ids = {path.stem for path in (root / "catalog" / "games").glob("*.yaml")}
    used_ids.update(game["game_id"] for plan in plans for game in plan.get("games", [])
                    if game.get("game_id"))
    for plan in plans:
        saved = _read(_campaign_dir(root, plan["campaign"]) / "plan.json")
        used_ids.update(game["game_id"] for game in saved.get("games", []))
    plans = [_prepare_games(plan, root, used_ids) for plan in plans]
    _check_unique([game for plan in plans for game in plan["games"]])
    for plan in plans:
        _check_plan(plan, stages)
        for game in plan["games"]:
            if game.get("task_spec"):
                load_specification(root / game["task_spec"], game["game_id"])
    player_stages = (["vlm_check"] if "vlm_check" in stages else []) + \
                    (["vlm_collect"] if "collect" in stages else [])
    models = {json.dumps([[plan[key]["model"], _player_thinking(plan[key], root)]
                          for key in player_stages]) for plan in plans}
    if len(models) > 1:
        raise ValueError("campaigns run together need the same vlm_check and vlm_collect models")
    setups = []
    for plan in plans:
        directory = _campaign_dir(root, plan["campaign"])
        _write(directory / "plan.json", plan)
        progress = _read(directory / "progress.json")
        if agents is None:
            profile = load_agent(plan["agent"])
            progress.update(agent=profile.name, model=profile.model, jobs=jobs)
        else:
            progress.update(agents={agent.name: agent.jobs for agent in agents})
        progress.update(campaign=plan["campaign"], stages=list(stages), status="running")
        setups.append((plan, directory, progress))
    campaigns = {directory: (progress, plan["games"]) for plan, directory, progress in setups}
    servers = []
    try:
        work = [(directory, _work(directory, plan["games"], stages, root))
                for plan, directory, _ in setups]
        games = [PoolGame(directory, game_id, plan["agent"])
                 for (plan, _, _), (directory, ids) in zip(setups, work) for game_id in ids]
        servers = _serve(setups[0][0], stages, work[0][1], directory=work[0][0], root=root, gpus=gpus,
                         wait_idle=wait_idle, vllm=vllm, others=work[1:])
        _run_pool(games, ["--root", str(root), "--from", stages[0], "--until", stages[-1]], agents=agents,
                  campaigns=campaigns, jobs=jobs, slot_base=slot_base)
    except BaseException:
        for plan, directory, progress in setups:
            progress["status"] = "interrupted"
            _assemble(progress, directory, plan["games"])
        raise
    finally:
        for server in servers:
            server.stop()

    results = {}
    for plan, directory, progress in setups:
        _assemble(progress, directory, plan["games"])
        progress["status"] = ("incomplete" if any(row.get("status") in INCOMPLETE
                                               for row in progress["games"].values()) else "completed")
        _write(directory / "progress.json", progress)
        results[plan["campaign"]] = progress
    return results


def run(plan: dict, *, root: Path, start: str = "scout", until: str = "collect",
        jobs: int = 1, gpus: str | None = None, wait_idle: bool = False,
        vllm: str | None = None, slot_base: int = 0) -> dict:
    return run_campaigns([plan], root=root, start=start, until=until, jobs=jobs, gpus=gpus,
                         wait_idle=wait_idle, vllm=vllm, slot_base=slot_base)[plan["campaign"]]


def _existing_game_worker(root: Path, game_id: str, *, proc_root: Path = Path("/proc")) -> int | None:
    """Recognize older workers, including ones started before leases were added."""
    def identity(path: Path) -> tuple[int, int]:
        fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            raise ValueError("process exited")
        return int(fields[19]), int(path.name)

    current = identity(proc_root / str(os.getpid()))
    root = root.resolve()
    for process in proc_root.iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid() or (process / "cwd").resolve() != root:
                continue
            args = [part.decode(errors="replace") for part in
                    (process / "cmdline").read_bytes().split(b"\0") if part]
            if args[1:3] != ["-m", "tools.game_pipeline"] or "--game" not in args:
                continue
            if args[args.index("--game") + 1] == game_id and identity(process) < current:
                return int(process.name)
        except (OSError, ValueError, IndexError):
            continue
    return None


@contextmanager
def _game_lease(root: Path, game_id: str):
    """Give one child exclusive ownership before it opens a game's state row."""
    if not game_id or Path(game_id).name != game_id:
        raise ValueError("game id must be one path component")
    directory = root / ".cache" / "game-pipeline" / "_game_leases"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{game_id}.lock").open("a") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            owner = _existing_game_worker(root, game_id)
            if owner is not None:
                print(f"[{game_id}] already running as PID {owner}; duplicate skipped", flush=True)
            yield owner is None
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path, nargs="+",
                        help="campaign YAML: agent, games or scout_prompt, stage settings; several with --agents")
    parser.add_argument("--root", type=Path, default=CODE_ROOT)
    parser.add_argument("--from", dest="start", choices=STAGES, default="scout")
    parser.add_argument("--until", choices=STAGES, default="collect")
    parser.add_argument("--jobs", type=int, default=1, help="games worked on at the same time")
    parser.add_argument("--agents", help="a pool of agents in order of preference, each with "
                                         "its games at a time: claude=2,codex=2 "
                                         "(instead of the plans' agent and --jobs)")
    parser.add_argument("--slot-base", type=int, default=0,
                        help="first rollout port block tried; blocks other runs on the machine "
                             "hold are skipped")
    parser.add_argument("--check-model", help="override vlm_check.model")
    parser.add_argument("--check-thinking", help="override vlm_check.thinking")
    parser.add_argument("--collect-model", help="override vlm_collect.model")
    parser.add_argument("--collect-thinking", help="override vlm_collect.thinking")
    parser.add_argument("--gpus", help="GPUs that serve a local model for the whole campaign")
    parser.add_argument("--wait-idle", action="store_true", help="wait until those GPUs are free")
    parser.add_argument("--vllm", help="vLLM executable (default: $GAMEENV_VLLM, else vllm on PATH)")
    parser.add_argument("--workers", type=int, help="model episodes at once per game (default 2)")
    parser.add_argument("--game", help="internal: run one game's stages")
    parser.add_argument("--slot", type=int, help="internal: the port block of that game's rollouts")
    parser.add_argument("--agent", help="internal: the agent that game runs on")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.slot_base < 0:
        parser.error("--slot-base must not be negative")
    if STAGES.index(args.start) > STAGES.index(args.until):
        parser.error("--from comes after --until")
    if len(args.plan) > 1 and not args.agents and not args.game:
        parser.error("several campaigns run together need --agents")
    # Termination arrives as KeyboardInterrupt, so the sessions stop their agent
    # processes, which run in sessions of their own and would otherwise survive.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    plans = []
    for path in args.plan:
        plan = yaml.safe_load(path.read_text())
        for key, name, value in (("vlm_check", "model", args.check_model),
                                 ("vlm_check", "thinking", args.check_thinking),
                                 ("vlm_collect", "model", args.collect_model),
                                 ("vlm_collect", "thinking", args.collect_thinking)):
            if value:
                plan.setdefault(key, {})[name] = value
        if args.workers:
            plan.setdefault("options", {})["vlm_workers"] = args.workers
        plans.append(plan)
    stages = STAGES[STAGES.index(args.start):STAGES.index(args.until) + 1]
    try:
        if args.game:
            with _game_lease(args.root, args.game) as owned:
                if not owned:
                    return 0
                row = GameRun(plans[0], args.game, root=args.root, slot=args.slot,
                              agent=args.agent).run(stages)
                return int(row.get("status") in INCOMPLETE)
        agents = parse_agents(args.agents) if args.agents else None
        results = run_campaigns(plans, root=args.root, start=args.start, until=args.until,
                                jobs=args.jobs, agents=agents, gpus=args.gpus,
                                wait_idle=args.wait_idle, vllm=args.vllm,
                                slot_base=args.slot_base)
    except KeyboardInterrupt:
        print("interrupted; rerun the same command to resume", flush=True)
        return 130
    failed = []
    for result in results.values():
        print(json.dumps({key: value for key, value in result.items() if key != "games"},
                         ensure_ascii=False, indent=2), flush=True)
        failed += [game_id for game_id, row in result["games"].items()
                   if row.get("status") in INCOMPLETE]
    if failed:
        print(f"failed: {', '.join(failed)}", flush=True)
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
