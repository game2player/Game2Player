"""A small persistent coding-agent loop for authoring game policies."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from catalog._yaml import load_yaml_mapping
from catalog.games._base import GameDefinition
from catalog.tasks._base import TaskSpec
from harnesses.policy.workspace import POLICY_WORKSPACE_ENV
from tools.coding_session import AgentProfile, CodingProtocolError, create_coding_session
from tools.debug_media import remove_debug_media
from tools.trajectory_schema import validate_episode


# Name the general skill each turn; the agent chooses specialised skills from
# its own inspection of the game and controls.
POLICY_SKILL = "$policy-author"


PHASES = ("DISCOVER", "IMPLEMENT", "EVALUATE", "DIAGNOSE", "REVISE")
TERMINAL_STATUSES = {"passed", "partial", "unsuitable", "blocked"}
RUN_TERMINAL_STATUSES = {*TERMINAL_STATUSES, "needs_environment"}
# A needs_environment request is either a broken shared contract, or observable state
# the probe cannot read without changing the game. Only the second is rationed: it
# asks Builder to add to the game rather than fix it, so one per experiment keeps the
# policy from drip-feeding requests instead of thinking through what it needs.
REQUEST_KINDS = ("none", "contract_defect", "observation")
MAX_OBSERVATION_REQUESTS = 1
MAX_PROTOCOL_REPAIRS = 2


class PolicyProtocolError(RuntimeError):
    """A policy-author turn returned a result outside its phase contract."""


class PolicyScopeError(RuntimeError):
    """A turn changed a file that defines this game or judges its result.

    Deliberately not a `PolicyProtocolError`: those are handed back to the run to
    correct, and handing this one back tells it to "restore" files whose current
    contents it never wrote. A run that starts while a protected file is being
    edited elsewhere, or that resumes across a commit, would then quietly undo
    that work -- which is how a stall mechanism and a backend fix were both lost.
    The run stops instead and the changes stay on disk for a person to judge.
    """


class PolicyEvidenceError(PolicyProtocolError):
    """A finish claim was rejected because its evidence does not support it.

    Separate from the other contract violations because the correction is not an
    edit to the response: the run has to go and record the missing episodes. A
    correction must request fresh episodes for the current policy rather than
    merely asking the agent to reword its ledger.
    """


RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "phase": {"type": "string", "enum": list(PHASES)},
        "status": {
            "type": "string",
            "enum": [
                "continue",
                "passed",
                "partial",
                "unsuitable",
                "blocked",
                "needs_environment",
            ],
        },
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "state_request": {"type": "string"},
        "request_kind": {
            "type": "string",
            "enum": list(REQUEST_KINDS),
        },
        "observation_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "meaning": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "meaning", "evidence"],
                "additionalProperties": False,
            },
        },
        "task_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["passed", "failed", "untested"],
                    },
                    "evidence": {"type": "string"},
                    "episode_dirs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["task_id", "status", "evidence", "episode_dirs"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "phase",
        "status",
        "summary",
        "evidence",
        "state_request",
        "request_kind",
        "observation_fields",
        "task_results",
    ],
    "additionalProperties": False,
}

PHASE_PROMPTS = {
    "DISCOVER": """DISCOVER only. Do not edit files. Inspect the game, exposed state,
controls, every configured task, reset behavior, visible information, and existing
policy/probe conventions. Before reverse engineering anything again, inspect the
existing game API, engine profile, readable source, and Builder notes described in
the main prompt. Decide whether a probe-backed policy is worthwhile. If suitable,
identify one shared algorithm and the probe fields it needs, aiming to support the
whole task set rather than selecting one easy task. Request environment refinement
for a demonstrated defect in the shared reset/task/evaluator contract, or, once per
experiment, for state the probe may expose but cannot read without changing the game.
Use that observation request before returning unsuitable for such missing state;
do not substitute a pixel-derived policy. If the state is concealed, cannot be
provided reliably, or the request was already used without resolving the blocker,
return unsuitable on that evidence.
Return continue, unsuitable, blocked, or needs_environment.""",
    "IMPLEMENT": """IMPLEMENT only. Create the smallest clear per-game policy probe
and shared Python policy. Reuse one implementation across
tasks; use reset(context) only for small task-specific parameters. Keep hidden or
future truth out of the probe. Perform cheap syntax/import checks, but do not run the
formal multi-task evaluation yet. Return continue or blocked.""",
    "EVALUATE": """EVALUATE only. Do not edit files. Use short focused headless
episodes. Work the current frontier task and compare its outcome and progress with
the best prior evidence. Widen to a harder task once the frontier is genuinely
solved, not to accumulate coverage: sweeping every task while the policy is still
broken buys nothing the finished-experiment check does not already require. After a
revision, retest the affected task before widening again. Before
finishing, make sure every task has the two recorded attempts the ledger asks for,
and that anything you intend to report as passed has two completed episodes under
the policy as it stands; use distinct seeds when the game has meaningful seeded
variation. Report all task attempts, including failures.
Report the tasks you have evidence for; a finished experiment must cover them all,
but an intermediate turn need not. Return continue only when another concrete
diagnosis and revision is justified.
Return passed for broad useful coverage, partial for a useful but insufficient
policy, and unsuitable when nothing useful remains. If the policy reaches the state
a task describes and its scored field still never moves, the build is blocking that
task: do not finish unsuitable on it, return continue so DIAGNOSE can report it as a
contract_defect. Do not continue merely because revision budget remains. Every episode command must use --headless, an output root
under the assigned run directory, and a bounded --duration-s -- normally at most
900, higher only when the previous episode hit the bound with progress still
climbing, or when the task's own difficulty tier is longer than that. Put the
real episode directories in task_results.episode_dirs. Return blocked only for an
external failure.""",
    "DIAGNOSE": """DIAGNOSE only. Do not edit files. Compare the focused failed
evaluation with the best prior run, inspect only the relevant states, inputs,
frames, and source, and select one concrete high-value hypothesis. Distinguish a
real plateau from an untried method or an obvious correctable fault. If there is a
specific promising direction, produce one narrow revision plan and return
continue. When the frontier plateaus, preserve the best shared behavior and move
the frontier to the next task rather than tuning a solved one; a task the policy
has not reached yet is a target, not a gap to be papered over. After every task has several attempts, return
passed only when total coverage is broad and useful; otherwise return partial when
useful tasks passed or unsuitable when none did. Request one necessary missing
shared-environment correction only when reset or trusted task verification is the
demonstrated blocker, including a scored field that never moves when the policy
reaches the state the task describes. Missing policy observations belong in the probe; request one
from Builder only when the probe may expose it but cannot read it without changing
the game, at most once per experiment. Use an available observation request for
such missing state before concluding unsuitable.""",
    "REVISE": """REVISE only. Apply one small coherent fix for the diagnosed
hypothesis to the same per-game policy. Avoid a broad rewrite or several unrelated
changes in one turn. Preserve already working behavior and the best-known policy;
if the focused evaluation regresses, the next revision should revert or replace
the failed idea rather than compound it. Do not add per-task modules. Run only
cheap focused checks; the next phase performs the gameplay evaluation. Return
continue or blocked.""",
}


@dataclass(slots=True)
class RunState:
    game_id: str
    phase: str = "DISCOVER"
    status: str = "running"
    thread_id: str | None = None
    revisions: int = 0
    turns: int = 0
    state_requests: int = 0
    observation_requests: int = 0
    pending_state_request: str = ""
    environment_handoff: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    last_error: str | None = None
    started_at: str = ""

    @classmethod
    def load(cls, path: Path, game_id: str) -> RunState:
        if not path.exists():
            return cls(game_id=game_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("game_id") != game_id:
            raise ValueError(
                f"Cached run belongs to {raw.get('game_id')!r}, not {game_id!r}."
            )
        return cls(
            game_id=game_id,
            phase=str(raw.get("phase") or "DISCOVER"),
            status=str(raw.get("status") or "running"),
            thread_id=raw.get("thread_id"),
            revisions=int(raw.get("revisions") or 0),
            turns=int(raw.get("turns") or 0),
            state_requests=int(raw.get("state_requests") or 0),
            observation_requests=int(raw.get("observation_requests") or 0),
            pending_state_request=str(raw.get("pending_state_request") or ""),
            environment_handoff=raw.get("environment_handoff"),
            history=list(raw.get("history") or []),
            started_at=str(raw.get("started_at") or ""),
            last_error=raw.get("last_error"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "game_id": self.game_id,
                    "phase": self.phase,
                    "status": self.status,
                    "thread_id": self.thread_id,
                    "revisions": self.revisions,
                    "turns": self.turns,
                    "state_requests": self.state_requests,
                    "observation_requests": self.observation_requests,
                    "pending_state_request": self.pending_state_request,
                    "environment_handoff": self.environment_handoff,
                    "history": self.history,
                    "started_at": self.started_at,
                    "last_error": self.last_error,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


class PolicyAuthor:
    """Drive one game's five-phase policy loop through one persistent session."""

    def __init__(
        self,
        game_id: str,
        *,
        root: Path | None = None,
        agent: AgentProfile,
        max_revisions: int = 50,
        turn_timeout_s: float = 90 * 60,
        full_access: bool = False,
        experiment: str | None = None,
        environment_handoff: Path | None = None,
        player_reports: list[dict[str, Any]] | None = None,
        fresh: bool = False,
        repair: bool = False,
    ):
        self.root = (root or Path(__file__).resolve().parents[2]).resolve()
        self.game_id = game_id.strip()
        if not self.game_id or Path(self.game_id).name != self.game_id:
            raise ValueError("game_id must be one catalog directory name")
        game_path = self.root / "catalog" / "games" / f"{self.game_id}.yaml"
        game = GameDefinition.from_mapping(load_yaml_mapping(game_path))
        if game.game_name != self.game_id:
            raise ValueError(f"Catalog identity does not match {self.game_id}")
        if not (self.root / "games" / "benchmark" / self.game_id).is_dir():
            raise ValueError(f"Game files are unavailable: {self.game_id}")
        if max_revisions < 0:
            raise ValueError("max_revisions must be non-negative")
        if fresh and repair:
            raise ValueError("fresh and repair are mutually exclusive")
        if environment_handoff is not None and (fresh or repair):
            raise ValueError("environment_handoff cannot be combined with fresh or repair")
        self.agent = agent
        self.max_revisions = max_revisions
        self.turn_timeout_s = turn_timeout_s
        self.full_access = full_access
        self.player_reports = list(player_reports or [])
        self.experiment = (experiment or datetime.now().strftime("%Y-%m-%d")).strip()
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
        if (
            not self.experiment
            or Path(self.experiment).name != self.experiment
            or any(character not in allowed for character in self.experiment)
        ):
            raise ValueError(
                "experiment may contain only lowercase letters, digits, '-' and '_'"
            )
        self.run_dir = (
            self.root
            / ".cache"
            / "policy-author"
            / "experiments"
            / self.experiment
            / self.game_id
        )
        existed = self.run_dir.exists()
        if (fresh or repair) and self.run_dir.exists():
            shutil.rmtree(self.run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir = self.run_dir / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        if repair or (not existed and not fresh):
            self._seed_workspace_from_promoted()
        self.state_path = self.run_dir / "state.json"
        self.state = RunState.load(self.state_path, self.game_id)
        if self.state.status not in RUN_TERMINAL_STATUSES:
            self._capture_scope_baseline()
        if environment_handoff is not None:
            self._accept_environment_handoff(environment_handoff)
        self.session = None
        if self.state.status not in RUN_TERMINAL_STATUSES:
            self.session = create_coding_session(
                agent=self.agent,
                root=self.root,
                run_dir=self.run_dir,
                thread_id=self.state.thread_id,
                turn_timeout_s=self.turn_timeout_s,
                full_access=self.full_access,
                environment={
                    **os.environ,
                    POLICY_WORKSPACE_ENV: str(self.workspace_dir),
                },
                on_thread_started=self._remember_thread,
            )

    def run(self) -> RunState:
        if self.state.status not in RUN_TERMINAL_STATUSES and not self.state.started_at:
            self.state.started_at = datetime.now(timezone.utc).isoformat()
            self.state.save(self.state_path)
        while self.state.status == "running":
            phase = self.state.phase
            print(f"[{self.game_id}] {phase} (turn {self.state.turns + 1})", flush=True)
            try:
                result = self._run_turn(phase)
            except (CodingProtocolError, PolicyProtocolError):
                self.state.status = "blocked"
                self.state.save(self.state_path)
                raise
            self._accept_result(result)
        if self.state.status in {"passed", "partial", "unsuitable"}:
            remove_debug_media(self.run_dir)
        return self.state

    def _skill_line(self) -> str:
        """Let the agent select engine/UI skills instead of guessing from files."""
        return (
            f"\n\nUse the repository skill {POLICY_SKILL} for this phase. "
            "Choose and read the relevant engine/UI policy skills in .agents/skills/ "
            "based on the game and controls you inspect."
        )

    def _prompt(self, phase: str) -> str:
        phase_prompt = PHASE_PROMPTS[phase]
        if self.player_reports:
            phase_prompt += (
                "\n\nUnconfirmed player reports (not established environment defects):\n"
                + json.dumps(self.player_reports, ensure_ascii=False, indent=2)
                + "\nUse relevant observations from your normal policy tests to investigate "
                "these reports. Do not change the game or tasks. If you establish a "
                "real environment defect, use the existing needs_environment request; "
                "otherwise describe the observed behavior in your evaluation evidence. "
                "Failing to solve a task does not confirm its report."
            )
        handoff = ""
        if phase == "DISCOVER" and self.state.environment_handoff:
            handoff = (
                "\n\nEnvironment Builder handoff:\n"
                + json.dumps(
                    self.state.environment_handoff,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\nReinspect the actual exposed state. Do not assume the request "
                "was fulfilled merely because Builder returned."
            )
        ledger = (
            self._evidence_ledger() if phase in {"EVALUATE", "DIAGNOSE"} else ""
        )
        skills = self._skill_line()
        if self.state.thread_id:
            return (
                f"Continue the same game.\nCurrent game id: `{self.game_id}`. "
                f"Working directory: `{self.run_dir}`.\n"
                f"Policy: `{self._policy_module_path}`; probe: `{self._policy_probe_path}`."
                f"{skills}\n\n{phase_prompt}{ledger}{handoff}"
            )
        template = (Path(__file__).with_name("prompt.md")).read_text(encoding="utf-8")
        base = template.format(
            game_id=self.game_id,
            run_dir=self.run_dir,
            policy_path=self._policy_module_path.relative_to(self.root),
            probe_path=self._policy_probe_path.relative_to(self.root),
        )
        return f"{base}{skills}\n\nCurrent phase:\n\n{phase_prompt}{ledger}{handoff}"


    # ---- evidence ledger -------------------------------------------------
    # Two counters decide whether a task may be reported, and they are counted
    # differently: an attempt is any episode ever recorded for the task, whatever
    # policy produced it and whether or not it succeeded, while a pass needs
    # successes from the policy exactly as it stands now.

    _MIN_ATTEMPTS = 2
    _MIN_PASSING_EPISODES = 2

    def _task_ids(self) -> list[str]:
        tasks = []
        for path in sorted((self.root / "catalog" / "tasks" / self.game_id).glob("*.yaml")):
            task = TaskSpec.from_mapping(load_yaml_mapping(path))
            if task.task_id != path.stem or task.game_id != self.game_id:
                raise ValueError(f"Task identity mismatch in {path}")
            tasks.append(task.task_id)
        return tasks

    def _episode_summaries(self) -> list[dict[str, Any]]:
        """Every episode recorded under this run, read from disk rather than reported.

        Scanning beats walking `history`, which holds only what the run chose to
        cite: an episode it recorded and did not mention still counts as an attempt,
        and telling it so is what stops it re-running work it has already done.
        """
        found: list[dict[str, Any]] = []
        for path in sorted(self.run_dir.rglob("summary.json")):
            try:
                summary = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(summary, dict) or summary.get("game_id") != self.game_id:
                continue
            found.append(summary)
        return found

    def _current_hashes(self) -> tuple[str | None, str | None]:
        try:
            policy = hashlib.sha256(self._policy_module_path.read_bytes()).hexdigest()
        except OSError:
            policy = None
        try:
            probe = hashlib.sha256(
                self._policy_probe_path.read_text(encoding="utf-8").strip().encode("utf-8")
            ).hexdigest()
        except OSError:
            probe = None
        return policy, probe

    def _attempt_counts(self) -> dict[str, int]:
        """Episodes ever recorded per task, regardless of policy version or outcome."""
        counts: dict[str, int] = {}
        for summary in self._episode_summaries():
            task = summary.get("task")
            task_id = task.get("id") if isinstance(task, dict) else None
            if task_id:
                counts[str(task_id)] = counts.get(str(task_id), 0) + 1
        return counts

    def _task_tally(self, known: list[str]) -> dict[str, dict[str, int]]:
        """Per task: every attempt on disk, the episodes of the current policy and
        probe, and how many of those completed the task."""
        policy_hash, probe_hash = self._current_hashes()
        tally = {
            task_id: {"attempts": 0, "current": 0, "completed": 0} for task_id in known
        }
        for summary in self._episode_summaries():
            task = summary.get("task")
            task_id = task.get("id") if isinstance(task, dict) else None
            row = tally.get(str(task_id))
            if row is None:
                continue
            row["attempts"] += 1
            actor = summary.get("actor")
            if not isinstance(actor, dict):
                continue
            if (
                policy_hash is not None
                and probe_hash is not None
                and actor.get("policy_sha256") == policy_hash
                and actor.get("policy_probe_sha256") == probe_hash
            ):
                row["current"] += 1
                result = summary.get("result")
                if isinstance(result, dict) and result.get("task_completed") is True:
                    row["completed"] += 1
        return tally

    def _evidence_ledger(self) -> str:
        known = self._task_ids()
        if not known:
            return ""
        policy_hash, probe_hash = self._current_hashes()
        tally = self._task_tally(known)

        minimum_passed = max(2, len(known) // 4 + 1) if len(known) > 1 else 1
        lines = [
            "",
            "",
            "Evidence ledger (computed from the episodes on disk, not from your reports)",
            "",
            f"Policy sha256 {(policy_hash or 'missing')[:16]}",
            f"Probe  sha256 {(probe_hash or 'missing')[:16]}",
            f"Passing this game needs {minimum_passed} of its {len(known)} tasks to pass.",
            "",
            f"{'task':<24}{'attempts':>9}{'current':>9}{'completed':>11}  finish requirement",
        ]
        blocking: list[str] = []
        for task_id in known:
            row = tally[task_id]
            short = row["attempts"] < self._MIN_ATTEMPTS
            if short:
                note = (
                    f"needs {self._MIN_ATTEMPTS - row['attempts']} more attempt(s) "
                    "before it can be reported"
                )
                blocking.append(task_id)
            elif row["completed"] >= self._MIN_PASSING_EPISODES:
                note = "may be reported passed or failed"
            else:
                note = "may be reported failed; passing needs 2 completed under this policy"
            lines.append(
                f"{task_id:<24}{row['attempts']:>9}{row['current']:>9}"
                f"{row['completed']:>11}  {note}"
            )
        lines.append("")
        if blocking:
            lines.append(
                "Returning passed or partial now would be rejected: "
                + ", ".join(blocking)
                + " have too few attempts."
            )
        else:
            lines.append(
                "Every task has enough attempts to be reported; a passing claim still "
                "needs two completed episodes under the policy as it stands now."
            )
        lines.append(
            "Editing the policy or the probe resets the current and completed columns "
            "to zero for every task. The attempts column is unaffected."
        )
        lines.append(self._stall_notice())
        return "\n".join(lines)

    def _elapsed_minutes(self) -> float | None:
        if not self.state.started_at:
            return None
        try:
            started = datetime.fromisoformat(self.state.started_at)
        except ValueError:
            return None
        return (datetime.now(timezone.utc) - started).total_seconds() / 60

    def _revisions_since_progress(self) -> int:
        """Revisions since a new pass or material recorded task progress."""
        best: set[str] = set()
        best_progress: dict[str, float] = {}
        episodes: dict[tuple[str, str], float | None] = {}
        revisions_at_growth = 0
        revisions = 0
        for entry in self.state.history:
            if entry.get("phase") == "REVISE":
                revisions += 1
                continue
            results = entry.get("task_results")
            if not isinstance(results, list):
                continue
            passing = {
                str(item.get("task_id"))
                for item in results
                if isinstance(item, dict) and item.get("status") == "passed"
            }
            improved = bool(passing - best)
            for item in results:
                if not isinstance(item, dict):
                    continue
                task_id = str(item.get("task_id") or "")
                for directory in item.get("episode_dirs") or []:
                    key = (task_id, str(directory))
                    if key not in episodes:
                        episodes[key] = self._recorded_progress(task_id, str(directory))
                    progress = episodes[key]
                    # One percentage point avoids treating tiny score noise as
                    # headroom. Completion claims still use the strict audit.
                    if progress is not None and progress >= best_progress.get(task_id, 0.0) + 0.01:
                        best_progress[task_id] = progress
                        improved = True
            if improved:
                best |= passing
                revisions_at_growth = revisions
        return revisions - revisions_at_growth

    def _recorded_progress(self, task_id: str, directory: str) -> float | None:
        """Read partial progress from a finished, played policy episode."""
        try:
            path = self._resolve_episode_dir(directory) / "summary.json"
            summary = json.loads(path.read_text(encoding="utf-8"))
            result = summary.get("result") or {}
            actor = summary.get("actor") or {}
            if (summary.get("game_id") != self.game_id
                    or (summary.get("task") or {}).get("id") != task_id
                    or summary.get("error") is not None
                    or result.get("outcome") not in {"success", "fail", "timeout"}
                    or actor.get("kind") != "policy"
                    or actor.get("steps", 0) <= 0
                    or (summary.get("inputs") or {}).get("count", 0) <= 2
                    or (summary.get("states") or {}).get("policy_coverage", 0) < 0.99):
                return None
            progress = result.get("progress")
            if (isinstance(progress, (int, float)) and not isinstance(progress, bool)
                    and math.isfinite(progress) and 0 <= progress <= 1):
                return float(progress)
        except (OSError, ValueError, TypeError, AttributeError, PolicyProtocolError):
            pass
        return None

    _STALL_REVISIONS = 3
    _STALL_MINUTES = 60.0
    # Past this, searching has stopped being the cheaper option and the run is told
    # to finish with the evidence it has rather than keep revising indefinitely.
    _FINISH_REVISIONS = 6
    _FINISH_MINUTES = 180.0

    def _stall_notice(self) -> str:
        """Interrupt a grind. Tuning that is not moving the ledger is not progress."""
        stuck = self._revisions_since_progress()
        minutes = self._elapsed_minutes()
        # Time alone is not a reason to interrupt: a run whose last revision moved the
        # ledger is working, however long it has been going. Elapsed time only lowers
        # the bar for how much standing still counts as stalling.
        long_run = minutes is not None and minutes >= self._STALL_MINUTES
        if stuck < (1 if long_run else self._STALL_REVISIONS):
            return ""
        spent = f"{minutes:.0f} minutes" if minutes is not None else "this run"
        if stuck >= self._FINISH_REVISIONS or (
            minutes is not None and minutes >= self._FINISH_MINUTES and stuck >= 2
        ):
            return (
                "\n"
                f"\nFinish now. {stuck} revision(s) over {spent} have produced neither a "
                "new passing task nor material recorded task progress. Continuing to search is no longer the cheaper option, and "
                "a run that reports nothing is worth less than one that reports what "
                "works.\n"
                "Report the tasks the ledger supports. If no tasks passed, return "
                "unsuitable and stop. If useful tasks passed, finish only the missing "
                "evidence required by the finish gate, using the current policy "
                f"unchanged: at least {self._MIN_ATTEMPTS} historical attempts for "
                "every configured task, and at least "
                f"{self._MIN_PASSING_EPISODES} completed current-policy episodes "
                "for each task you call passed. Failed attempts and attempts under "
                "earlier policy versions count toward the historical minimum; do not "
                "repeat failed tasks that already meet it. These bounded evidence "
                "runs are verification, not another search for a better policy. "
                "Do not revise the policy again unless a "
                "revision is required to make an already-passing task reproducible."
            )
        return (
            "\n"
            f"\nStep back. {stuck} revision(s) have produced neither a new pass nor material recorded task progress, over "
            f"{spent}. Before writing another change, answer these in your summary:\n"
            "  - Is the current approach capable of this task at all, or are you tuning "
            "a controller whose shape is wrong? A different observation, a different "
            "target-selection rule, or a different action pattern is a real option and "
            "is cheaper than five more thresholds.\n"
            "  - Is the last few revisions' history a sequence of changes and reverts "
            "around one best-known version? If so you are at that version's ceiling.\n"
            "  - What does the ledger already support? Finishing with the tasks that "
            "pass is a result. A run that spends hours on its last task and reports "
            "nothing is worth less than one that reports what works.\n"
            "Continue only if you can name a specific untried mechanism, not another "
            "value for a constant you have already moved."
        )

    def _run_turn(self, phase: str) -> dict[str, Any]:
        prompt = self._prompt(phase)
        last_error: Exception | None = None
        for attempt in range(MAX_PROTOCOL_REPAIRS + 1):
            try:
                try:
                    result = self.session.turn(
                        prompt,
                        RESULT_SCHEMA,
                        sandbox="workspace-write",
                    )
                finally:
                    # A malformed response or failed process must not skip the guard.
                    self._check_edit_scope()
                self._validate_result(result, phase)
                return result
            except (CodingProtocolError, PolicyProtocolError) as exc:
                last_error = exc
                if attempt >= MAX_PROTOCOL_REPAIRS or not self.session.thread_id:
                    break
                prompt = (
                    "The runner rejected your previous response before advancing the "
                    f"{phase} phase:\n\n{exc}\n\n"
                )
                if isinstance(exc, PolicyEvidenceError):
                    prompt += (
                        "This is a missing-evidence rejection, not a formatting one: "
                        "rewording the ledger cannot satisfy it. Every task needs at "
                        "least two recorded attempts (older versions also count). "
                        "A task reported passed additionally needs two completed episodes "
                        "under the policy and probe exactly as they are on disk now. Record the "
                        "missing episodes in this turn, then report the ledger again. "
                        "Do not edit the policy or the probe while doing so: an edit "
                        "invalidates earlier success evidence for the current version. If a task "
                        "genuinely cannot produce two episodes, report it as failed "
                        "with the attempts you did record rather than omitting it."
                    )
                else:
                    prompt += (
                        "Correct the contract violation in the same phase. Preserve the "
                        "task and policy semantics and return only the required "
                        "structured result."
                    )
            except (OSError, RuntimeError, TimeoutError) as exc:
                self.state.last_error = str(exc)
                self.state.save(self.state_path)
                raise
        assert last_error is not None
        self.state.last_error = str(last_error)
        self.state.save(self.state_path)
        raise last_error

    def _remember_thread(self, thread_id: str) -> None:
        self.state.thread_id = thread_id
        self.state.save(self.state_path)

    def _validate_result(self, result: Any, phase: str) -> None:
        if not isinstance(result, dict):
            raise PolicyProtocolError("coding-agent response must be a JSON object")
        if result.get("phase") != phase:
            raise PolicyProtocolError(
                f"coding agent reported phase {result.get('phase')!r}; expected {phase!r}"
            )
        status = result.get("status")
        if status not in {"continue", "needs_environment", *TERMINAL_STATUSES}:
            raise PolicyProtocolError(
                f"invalid coding-agent status: {result.get('status')!r}"
            )
        if status in {"passed", "partial"} and phase not in {
            "EVALUATE",
            "DIAGNOSE",
        }:
            raise PolicyProtocolError(
                "Only EVALUATE or DIAGNOSE may finish a policy experiment"
            )
        if not isinstance(result.get("summary"), str):
            raise PolicyProtocolError("coding-agent summary must be a string")
        if not isinstance(result.get("evidence"), list):
            raise PolicyProtocolError("coding-agent evidence must be a list")
        state_request = result.get("state_request")
        if not isinstance(state_request, str):
            raise PolicyProtocolError("coding-agent state_request must be a string")
        request_kind = result.get("request_kind")
        if request_kind not in REQUEST_KINDS:
            raise PolicyProtocolError(
                f"request_kind must be one of {', '.join(REQUEST_KINDS)}"
            )
        fields = result.get("observation_fields")
        if not isinstance(fields, list):
            raise PolicyProtocolError("coding-agent observation_fields must be a list")
        if status == "needs_environment":
            if phase not in {"DISCOVER", "DIAGNOSE"}:
                raise PolicyProtocolError(
                    "environment state may be requested only from DISCOVER or DIAGNOSE"
                )
            if not state_request.strip():
                raise PolicyProtocolError(
                    "needs_environment requires a concrete state_request"
                )
            if request_kind == "none":
                raise PolicyProtocolError(
                    "needs_environment requires request_kind contract_defect or observation"
                )
        elif state_request.strip() or request_kind != "none":
            raise PolicyProtocolError(
                "state_request must be empty and request_kind none unless status "
                "needs_environment"
            )
        if request_kind == "observation":
            if not fields or not all(
                isinstance(field, dict) and str(field.get("name") or "").strip()
                for field in fields
            ):
                raise PolicyProtocolError(
                    "an observation request must name each field it needs in "
                    "observation_fields"
                )
            if self.state.observation_requests >= MAX_OBSERVATION_REQUESTS:
                raise PolicyProtocolError(
                    "This experiment has already used its one observation request. "
                    "Work with the observation channel that request produced, or "
                    "finish with unsuitable on the named blocker."
                )
        elif fields:
            raise PolicyProtocolError(
                "observation_fields must be empty unless request_kind is observation"
            )
        task_results = result.get("task_results")
        if not isinstance(task_results, list):
            raise PolicyProtocolError("coding-agent task_results must be a list")
        known_tasks = set(self._task_ids())
        seen_tasks: set[str] = set()
        passed_results: list[dict[str, Any]] = []
        for task_result in task_results:
            if not isinstance(task_result, dict):
                raise PolicyProtocolError("Each task result must be an object")
            task_id = str(task_result.get("task_id") or "").strip()
            if task_id not in known_tasks:
                raise PolicyProtocolError(f"Unknown task id in task_results: {task_id!r}")
            if task_id in seen_tasks:
                raise PolicyProtocolError(f"Duplicate task result: {task_id}")
            seen_tasks.add(task_id)
            episode_dirs = task_result.get("episode_dirs")
            if not isinstance(episode_dirs, list) or not all(
                isinstance(item, str) for item in episode_dirs
            ):
                raise PolicyProtocolError(
                    f"task_results[{task_id}].episode_dirs must be a string list"
                )
            if task_result.get("status") == "passed":
                passed_results.append(task_result)

        if status in {"passed", "partial"}:
            missing_tasks = sorted(known_tasks - seen_tasks)
            if missing_tasks:
                raise PolicyEvidenceError(
                    "A finished experiment must report every task: "
                    + ", ".join(missing_tasks)
                )
            unfinished = sorted(
                str(item["task_id"])
                for item in task_results
                if item.get("status") == "untested"
            )
            if unfinished:
                raise PolicyEvidenceError(
                    "A finished experiment cannot leave tasks untested: "
                    + ", ".join(unfinished)
                )
            if not passed_results:
                raise PolicyProtocolError(
                    "A useful finished policy requires at least one passed task"
                )
            # Two tasks is the floor, and it scales with the set: a
            # seventeen-task game passing two of them is not a passing policy.
            minimum_passed = max(2, len(known_tasks) // 4 + 1)
            if status == "passed" and len(known_tasks) > 1 and len(passed_results) < minimum_passed:
                raise PolicyEvidenceError(
                    f"A passing policy for {len(known_tasks)} tasks needs at least "
                    f"{minimum_passed} passed tasks, not {len(passed_results)}; "
                    "return partial or continue improving it"
                )
            # Two counters, counted differently. An attempt is any episode ever
            # recorded for the task, whichever policy produced it and whether or not
            # it succeeded, so a task the run genuinely could not reach is settled by
            # having tried rather than by re-running it against a final policy that
            # will fail it again. A pass is stricter: the successes have to come from
            # the policy exactly as it stands now, because that is the policy the run
            # ships. Both come from the same scan the ledger shows the run each turn,
            # so what it is told and what it is judged on cannot drift apart.
            attempts = self._attempt_counts()
            shortfalls: list[str] = []
            for task_result in task_results:
                task_id = str(task_result["task_id"])
                recorded = attempts.get(task_id, 0)
                if recorded < self._MIN_ATTEMPTS:
                    shortfalls.append(
                        f"{task_id}: {recorded} episode(s) recorded, "
                        f"{self._MIN_ATTEMPTS} attempts required"
                    )
                    continue
                if task_result.get("status") != "passed":
                    continue
                episode_dirs = list(dict.fromkeys(
                    str(self._resolve_episode_dir(directory))
                    for directory in task_result["episode_dirs"]
                ))
                completed = sum(
                    self._validate_evaluation_episode(
                        task_id=task_id,
                        episode_dir=episode_dir,
                    )
                    for episode_dir in episode_dirs
                )
                if completed < self._MIN_PASSING_EPISODES:
                    shortfalls.append(
                        f"{task_id}: marked passed with {completed} completed "
                        f"episode(s) under the current policy, "
                        f"{self._MIN_PASSING_EPISODES} required"
                    )
            if shortfalls:
                raise PolicyEvidenceError(
                    "The evidence does not support this finish claim:\n  "
                    + "\n  ".join(shortfalls)
                )

    def _accept_result(self, result: dict[str, Any]) -> None:
        phase = self.state.phase
        status = str(result["status"])
        self.state.turns += 1
        self.state.history.append(result)
        self.state.last_error = None

        if status == "needs_environment":
            self.state.state_requests += 1
            if result["request_kind"] == "observation":
                self.state.observation_requests += 1
            self.state.pending_state_request = str(result["state_request"]).strip()
            self.state.status = "needs_environment"
            self.state.last_error = "shared environment refinement requested"
            self._write_environment_request(result, phase)
        elif status in TERMINAL_STATUSES:
            if status in {"passed", "partial"}:
                self._promote_policy()
            self.state.status = status
        elif phase == "DISCOVER":
            self.state.environment_handoff = None
            self.state.phase = "IMPLEMENT"
        elif phase == "IMPLEMENT":
            self.state.phase = "EVALUATE"
        elif phase == "EVALUATE":
            if self.state.revisions >= self.max_revisions:
                self.state.status = "blocked"
                self.state.last_error = "revision limit reached without a passing evaluation"
            else:
                self.state.phase = "DIAGNOSE"
        elif phase == "DIAGNOSE":
            self.state.phase = "REVISE"
        elif phase == "REVISE":
            self.state.revisions += 1
            self.state.phase = "EVALUATE"
        else:
            raise RuntimeError(f"Unknown phase: {phase}")
        self.state.save(self.state_path)
        print(f"[{self.game_id}] {result['summary']}", flush=True)

    def _resolve_episode_dir(self, episode_dir: str) -> Path:
        path = Path(episode_dir).expanduser()
        if not path.is_absolute():
            run_relative = self.run_dir / path
            root_relative = self.root / path
            path = run_relative if run_relative.exists() else root_relative
        path = path.resolve()
        try:
            path.relative_to(self.run_dir)
        except ValueError as exc:
            raise PolicyProtocolError(
                f"Evaluation episode must be under {self.run_dir}: {path}"
            ) from exc
        return path

    def _validate_evaluation_episode(
        self, *, task_id: str, episode_dir: str
    ) -> bool:
        path = self._resolve_episode_dir(episode_dir)
        try:
            validate_episode(path)
            summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise PolicyProtocolError(f"Invalid evaluation episode {path}: {exc}") from exc

        if summary.get("game_id") != self.game_id:
            raise PolicyProtocolError(f"Episode {path} belongs to another game")
        task = summary.get("task")
        if not isinstance(task, dict) or task.get("id") != task_id:
            raise PolicyProtocolError(f"Episode {path} does not evaluate task {task_id}")
        actor = summary.get("actor")
        if not isinstance(actor, dict) or actor.get("type") != "policy":
            raise PolicyProtocolError(f"Episode {path} was not generated by a policy")
        try:
            actor_steps = int(actor.get("steps") or 0)
        except (TypeError, ValueError) as exc:
            raise PolicyProtocolError(f"Episode {path} has invalid policy steps") from exc
        if actor_steps <= 0:
            raise PolicyProtocolError(f"Episode {path} contains no policy actions")
        try:
            expected_policy_hash = hashlib.sha256(
                self._policy_module_path.read_bytes()
            ).hexdigest()
            expected_probe_hash = hashlib.sha256(
                self._policy_probe_path.read_text(encoding="utf-8")
                .strip()
                .encode("utf-8")
            ).hexdigest()
        except OSError as exc:
            raise PolicyProtocolError(
                "Passing policy requires both the policy module and policy probe"
            ) from exc
        if actor.get("policy_sha256") != expected_policy_hash:
            raise PolicyProtocolError(
                f"Episode {path} was generated by a different policy version"
            )
        if actor.get("policy_probe_sha256") != expected_probe_hash:
            raise PolicyProtocolError(
                f"Episode {path} was generated by a different policy probe version"
            )
        states = summary.get("states")
        try:
            coverage = (
                float(states.get("coverage") or 0.0)
                if isinstance(states, dict)
                else 0.0
            )
        except (TypeError, ValueError) as exc:
            raise PolicyProtocolError(f"Episode {path} has invalid state coverage") from exc
        if not math.isfinite(coverage) or coverage < 0.9:
            raise PolicyProtocolError(f"Episode {path} has insufficient state coverage")
        try:
            policy_coverage = (
                float(states.get("policy_coverage") or 0.0)
                if isinstance(states, dict)
                else 0.0
            )
        except (TypeError, ValueError) as exc:
            raise PolicyProtocolError(
                f"Episode {path} has invalid policy-state coverage"
            ) from exc
        if not math.isfinite(policy_coverage) or policy_coverage < 0.9:
            raise PolicyProtocolError(
                f"Episode {path} has insufficient policy-state coverage"
            )
        result = summary.get("result")
        if not isinstance(result, dict):
            raise PolicyProtocolError(f"Episode {path} has no evaluator result")
        if summary.get("error") is not None:
            raise PolicyProtocolError(f"Episode {path} reports a recording error")
        metrics = result.get("metrics")
        if (
            result.get("outcome") not in {"success", "fail", "timeout"}
            or not isinstance(metrics, dict)
            or metrics.get("finalized") is not True
        ):
            raise PolicyProtocolError(f"Episode {path} does not have a finalized evaluator result")
        return result.get("task_completed") is True

    @property
    def _policy_dir(self) -> Path:
        return self.workspace_dir / "policies"

    @property
    def _policy_module_path(self) -> Path:
        stem = self.game_id.replace("-", "_")
        return self._policy_dir / f"g{stem}.py"

    @property
    def _policy_probe_path(self) -> Path:
        stem = self.game_id.replace("-", "_")
        return self.workspace_dir / "probes" / f"g{stem}.js"

    @property
    def _promoted_policy_module_path(self) -> Path:
        stem = self.game_id.replace("-", "_")
        return self.root / "harnesses" / "policy" / "policies" / f"g{stem}.py"

    @property
    def _promoted_policy_probe_path(self) -> Path:
        stem = self.game_id.replace("-", "_")
        return self.root / "harnesses" / "policy" / "probes" / f"g{stem}.js"

    @property
    def _promoted_policy_tasks_path(self) -> Path:
        return self._promoted_policy_module_path.with_suffix(".tasks.json")

    def _capture_scope_baseline(self) -> None:
        """Snapshot the protected files as this process found them.

        In memory, and only for as long as this process lives: the question the
        check has to answer is whether these turns changed a protected file, and a
        snapshot kept on disk answers a different one -- whether anything changed
        since some earlier process ran -- which is how a resumed run came to call a
        commit made between its two starts a violation.
        """
        self._scope_baseline = self._watched_digests()

    # Everything a run could edit to make its own result dishonest: this game's
    # definition and assets, the evaluator, and the recording/validation path.
    # Anything else in the repository is irrelevant to whether this game's claim
    # is true, and watching it only produced false positives and blocked
    # concurrent runs.
    _SHARED_WATCHED = (
        "env",
        "catalog/_yaml.py",
        "catalog/games/_base.py",
        "catalog/tasks/_base.py",
        "harnesses/actions.py",
        "harnesses/base.py",
        "harnesses/registry.py",
        "harnesses/policy/harness.py",
        "harnesses/policy/probe.py",
        "harnesses/policy/workspace.py",
        "harnesses/policy/policies/__init__.py",
        "harnesses/policy/policies/base.py",
        "tools/policy_author",
        "tools/session_runner",
        "tools/rollout",
        "tools/trajectory_recorder",
        "tools/trajectory_schema.py",
        "play.py",
    )
    _LARGE_FILE_BYTES = 4 * 1024 * 1024

    def _watched_paths(self) -> list[Path]:
        game_specific = (
            self.root / "games" / "benchmark" / self.game_id,
            self.root / "catalog" / "games" / f"{self.game_id}.yaml",
            self.root / "catalog" / "tasks" / self.game_id,
            self.root / "catalog" / "unity" / f"{self.game_id}.yaml",
            self._promoted_policy_module_path,
            self._promoted_policy_probe_path,
            self._promoted_policy_tasks_path,
        )
        shared = (self.root / relative for relative in self._SHARED_WATCHED)
        return [*game_specific, *shared]

    def _watched_digests(self) -> dict[str, str]:
        """Per-file fingerprints of the protected set, keyed by repo-relative path.

        Only this game's own folders plus the shared runtime are covered, so two
        games can be authored concurrently without seeing each other's work.
        Packaged game bundles are fingerprinted by size and mtime rather than
        content; hashing a 200 MiB archive every turn is not worth the certainty.
        """
        digests: dict[str, str] = {}
        for target in self._watched_paths():
            if target.is_file():
                files = [target]
            elif target.is_dir():
                files = sorted(path for path in target.rglob("*") if path.is_file())
            else:
                continue
            for path in files:
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                relative = path.relative_to(self.root).as_posix()
                if relative in self._allowed_policy_paths():
                    continue
                stat = path.stat()
                if stat.st_size > self._LARGE_FILE_BYTES:
                    digests[relative] = f"meta:{stat.st_size}:{stat.st_mtime_ns}"
                else:
                    digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return digests

    def _allowed_policy_paths(self) -> set[str]:
        return {
            self._policy_module_path.relative_to(self.root).as_posix(),
            self._policy_probe_path.relative_to(self.root).as_posix(),
        }

    def _check_edit_scope(self) -> None:
        after = self._watched_digests()
        changed = sorted(
            path
            for path in set(self._scope_baseline) | set(after)
            if self._scope_baseline.get(path) != after.get(path)
        )
        if not changed:
            return
        raise PolicyScopeError(
            "Protected files changed during this run, and they define this game or "
            "judge its result:\n  "
            + "\n  ".join(changed)
            + "\n\nThe run stopped and restored nothing; the changes are still on "
            "disk. Only "
            + ", ".join(sorted(self._allowed_policy_paths()))
            + " may change while it works.\n"
            "Read the diff before restarting. If the run made these edits, undo them "
            "first; if they are someone else's, restarting takes them as the new "
            "baseline and continues from this phase."
        )

    def _seed_workspace_from_promoted(self) -> None:
        """Start a new repair workspace from the currently promoted artifacts."""
        for source, destination in (
            (self._promoted_policy_module_path, self._policy_module_path),
            (self._promoted_policy_probe_path, self._policy_probe_path),
        ):
            if source.is_file() and not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    def _promote_policy(self) -> None:
        """Export a useful policy and its completed tasks to the runtime paths."""
        try:
            policy = self._policy_module_path.read_bytes()
            probe = self._policy_probe_path.read_bytes()
        except OSError as exc:
            raise PolicyProtocolError(
                "A useful finished experiment requires both a policy and a probe"
            ) from exc
        for destination, content in (
            (self._promoted_policy_module_path, policy),
            (self._promoted_policy_probe_path, probe),
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)
        # Use the same success count as task acceptance, so a single successful
        # attempt does not silently add an unverified task to collection.
        completed = [task_id for task_id, row in self._task_tally(self._task_ids()).items()
                     if row["completed"] >= self._MIN_PASSING_EPISODES]
        temporary = self._promoted_policy_tasks_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(completed) + "\n", encoding="utf-8")
        temporary.replace(self._promoted_policy_tasks_path)
        print(
            f"[{self.game_id}] exported policy from {self.workspace_dir}",
            flush=True,
        )

    @property
    def environment_request_path(self) -> Path | None:
        if not self.state.pending_state_request or self.state.state_requests <= 0:
            return None
        return (
            self.run_dir
            / "environment"
            / f"request_{self.state.state_requests:02d}.json"
        )

    def _write_environment_request(
        self, result: dict[str, Any], phase: str
    ) -> None:
        path = self.environment_request_path
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        request = self.state.pending_state_request
        path.write_text(
            json.dumps(
                {
                    "game_id": self.game_id,
                    "experiment": self.experiment,
                    "request_id": self.state.state_requests,
                    "policy_phase": phase,
                    "request_kind": result["request_kind"],
                    "request": request,
                    "observation_fields": list(result["observation_fields"]),
                    "summary": str(result.get("summary") or ""),
                    "evidence": list(result.get("evidence") or []),
                    "suggested_builder_run_id": (
                        f"policy-{self.experiment}-request-"
                        f"{self.state.state_requests:02d}"
                    ),
                    "builder_brief": self._builder_brief(
                        request, result["request_kind"], result["observation_fields"]
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _builder_brief(
        request: str, kind: str = "contract_defect", fields: list[Any] | None = None
    ) -> str:
        if kind == "observation":
            listed = "\n".join(
                f"- {field.get('name')}: {field.get('meaning')} "
                f"(visible: {field.get('evidence')})"
                for field in fields or []
                if isinstance(field, dict)
            )
            return f"""A downstream Policy Author needs game state that its probe is allowed to see but
cannot read from the page without changing the game:

{request}

Fields requested:
{listed}

Publish exactly these fields from `game_api.js` as
`window.__policyObservation = {{ getState() {{ ... }} }}`. `getState()` returns a
plain JSON object keyed by the requested names, with null for a field the game has not
produced yet. Install it whenever the page loads, so it survives a reset.

This adds to the game; it does not repair it. Do not change gameplay, controls,
timing, difficulty or randomness, and do not touch `window.gameAPI`, its state, the
tasks or the evaluator; keep these fields out of `gameAPI.getState()`. Publish only
present or past state of the kind the probe may already expose -- geometry, entities,
velocities, board contents, health, cards the player can see -- never deliberately
concealed unknowns or future truth: concealed cards or cells, secret answers, the next
random result, future spawn schedules, or equivalents.

If a field can already be read from the page without modifying the game, do not
implement it; say in the summary exactly where and how the probe can read it. If a
field is concealed or cannot be published reliably, leave it out and say why. In the
summary, account for every requested field: published, readable already (and how),
or not provided (and why). When no field can be published, leave the environment
unchanged and return passed with that account."""
        return f"""A downstream Policy Author demonstrated this shared environment defect:

{request}

Repair only the smallest trusted reset, task, state, or evaluator contract needed
to address it. This state remains Builder-owned and must not be tailored to make
the scripted policy easier; policy-only observations belong in its separate probe.
Do not weaken task ids, objectives, starts, progress meanings, success conditions,
or difficulty. If the request is misleading, infeasible, or would require fragile
instrumentation, preserve the environment, say why in the summary and return passed
when the environment still meets the bar."""

    def _accept_environment_handoff(self, handoff: Path) -> None:
        if (
            self.state.status != "needs_environment"
            or not self.state.pending_state_request
        ):
            raise ValueError("this policy experiment has no pending environment request")
        source = handoff.expanduser().resolve()
        state_path = source / "state.json" if source.is_dir() else source
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Builder handoff: {state_path}") from exc
        if raw.get("game_id") != self.game_id:
            raise ValueError("Builder handoff belongs to another game")
        builder_status = str(raw.get("status") or "")
        # A Builder that declines a misleading or infeasible request still returns
        # `passed` with the reason in its summary, and DISCOVER re-inspects the
        # contract either way.
        if builder_status != "passed":
            raise ValueError(f"Builder handoff must be passed, not {builder_status!r}")
        history = raw.get("history")
        terminal = history[-1] if isinstance(history, list) and history else {}
        if not isinstance(terminal, dict):
            terminal = {}
        record = {
            "game_id": self.game_id,
            "experiment": self.experiment,
            "request_id": self.state.state_requests,
            "request": self.state.pending_state_request,
            "builder_run_dir": str(state_path.parent),
            "builder_status": builder_status,
            "summary": str(terminal.get("summary") or ""),
            "evidence": list(terminal.get("evidence") or []),
            "state_signals": list(terminal.get("state_signals") or []),
        }
        destination = (
            self.run_dir
            / "environment"
            / f"handoff_{self.state.state_requests:02d}.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.pending_state_request = ""
        self.state.environment_handoff = record
        self.state.status = "running"
        self.state.phase = "DISCOVER"
        self.state.last_error = None
        self._capture_scope_baseline()
        self.state.save(self.state_path)


__all__ = [
    "PHASES",
    "PolicyAuthor",
    "RESULT_SCHEMA",
    "RunState",
]
