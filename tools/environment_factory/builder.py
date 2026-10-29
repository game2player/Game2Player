"""Persistent one-game environment construction agent."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from catalog.tasks._base import TASK_ID_PATTERN, expected_task_id_prefix
from tools.coding_session import AgentProfile, CodingProtocolError, create_coding_session
from tools.environment_factory.artifact_gate import validate_artifact
from tools.environment_factory.specification import (
    load_specification, prepare_specification, specification_errors,
)


PHASES = ("DISCOVER", "IMPLEMENT", "EVALUATE", "REVISE")
TERMINAL_STATUSES = {"passed", "unsuitable"}
TASK_CONTRACT_STATUSES = {"verified", "pending", "unsupported"}
SIGNAL_PROVENANCES = (
    "internal",
    "engine_hook",
    "storage",
    "dom",
    "visual_estimate",
    "unavailable",
)
NONVISUAL_PROVENANCES = frozenset(SIGNAL_PROVENANCES[:4])
MAX_PROTOCOL_REPAIRS = 2
# Every engine label DISCOVER may report. Widened past the four that have skills so
# the record can name what a game actually is: a GameMaker or Godot export forced to
# report "html5" is not a lie the runner can count. An engine with no skill falls
# back to "identify the engine and load the matching skill", which is the behaviour
# an unmapped engine already had.
ENGINES = (
    "html5",
    "unity_webgl",
    "flash",
    "pico8",
    "gamemaker_html5",
    "godot",
    "construct",
    "defold",
    "unknown",
)
BUILDER_SKILL = "$builder"
ENGINE_SKILLS = {
    "unity_webgl": "$unity-webgl",
    "flash": "$flash",
    "pico8": "$pico8",
    "html5": "$html5-capture",
    "gamemaker_html5": "$gamemaker",
    "godot": "$godot",
    "construct": "$construct",
    "defold": "$defold",
}


class BuilderProtocolError(RuntimeError):
    """A Builder turn returned a result that violates its phase contract."""

COMMON_RESULT_PROPERTIES: dict[str, Any] = {
    "phase": {"type": "string", "enum": list(PHASES)},
    "status": {
        "type": "string",
        "enum": ["continue", "passed", "unsuitable"],
    },
    "engine": {
        "type": "string",
        "enum": list(ENGINES),
    },
    "summary": {"type": "string"},
    "evidence": {"type": "array", "items": {"type": "string"}},
}

DISCOVERY_PROPERTIES: dict[str, Any] = {
    "task_plan": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "objective": {"type": "string"},
                "start": {"type": "string"},
                "progress": {"type": "string"},
                "progress_signal": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        # How finely this field resolves the run. The normalized
                        # 0-1 `progress` the evaluator computes is only as
                        # informative as the field underneath it: a counter that
                        # spans 0..6 yields seven possible values, and when every
                        # agent fails a task, where on the curve they died is the
                        # only thing left to compare.
                        "granularity": {
                            "type": "string",
                            "enum": ["continuous", "dense", "counter", "flag"],
                        },
                        # For counter and flag, explain why this native measure
                        # represents the task. Empty for continuous and dense.
                        "coarse_reason": {"type": "string"},
                    },
                    "required": ["field", "granularity", "coarse_reason"],
                    "additionalProperties": False,
                },
                "success": {"type": "string"},
                "difficulty": {
                    "type": "string",
                    "enum": ["easy", "medium", "hard", "very_hard"],
                },
                "evidence": {"type": "string"},
            },
            "required": [
                "task_id",
                "objective",
                "start",
                "progress",
                "progress_signal",
                "success",
                "difficulty",
                "evidence",
            ],
            "additionalProperties": False,
        },
    },
}

EVALUATION_PROPERTIES: dict[str, Any] = {
    # Frozen tasks that cannot be delivered honestly: the shipped game itself can
    # never complete them (a success threshold above a value the game caps), or their
    # contract cannot be instrumented honestly. Retiring one removes it rather than
    # weakening it, so the game is judged on the remaining tasks.
    "retired_tasks": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "reason": {"type": "string"},
                "record": {
                    "type": "string",
                    "description": "One existing evidence file path relative to the Builder run directory, for example eval2/check.json. No prose, backticks, directory paths, or lists of files. Put additional references in evidence.",
                },
            },
            "required": ["task_id", "reason", "record"],
            "additionalProperties": False,
        },
    },
    # The players' side, checked the way they meet it: the screenshot at the catalog
    # window size, and each control the guide documents sent as players send it. The
    # guide and the window are this Builder's product, so no player is relied on to
    # discover that they mislead. Every item carries a record a reviewer can open.
    "player_view": {
        "type": "object",
        "properties": {
            "screen": {"type": "string", "enum": ["verified", "defect"]},
            "screen_record": {
                "type": "string",
                "description": "One existing screenshot or screen-check record file path relative to the Builder run directory, for example eval2/screen.png. No prose, backticks, directory paths, ranges, or lists of files. Put additional references in evidence.",
            },
            "controls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "control": {"type": "string"},
                        "sent": {"type": "string"},
                        "observed": {"type": "string"},
                        "status": {"type": "string", "enum": ["verified", "defect"]},
                        "record": {
                            "type": "string",
                            "description": "One existing evidence file path relative to the Builder run directory, for example eval2/control.json. No prose, backticks, directory paths, or semicolon-separated files. Use separate control entries for separate records, or put additional references in evidence.",
                        },
                    },
                    "required": ["control", "sent", "observed", "status", "record"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["screen", "screen_record", "controls"],
        "additionalProperties": False,
    },
    "blocker": {"type": "string"},
    "state_signals": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "meaning": {"type": "string"},
                "provenance": {
                    "type": "string",
                    "enum": list(SIGNAL_PROVENANCES),
                },
                "evidence": {"type": "string"},
            },
            "required": ["field", "meaning", "provenance", "evidence"],
            "additionalProperties": False,
        },
    },
    "task_contracts": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "design": {
                    "type": "string",
                    "enum": ["verified", "unsupported"],
                },
                "start": {
                    "type": "string",
                    "enum": ["verified", "pending", "unsupported"],
                },
                "progress": {
                    "type": "string",
                    "enum": ["verified", "pending", "unsupported"],
                },
                "terminal": {
                    "type": "string",
                    "enum": ["verified", "pending", "unsupported"],
                },
                # Null unless `progress` is verified. A self-reported
                # "verified" must be backed by a recorded transition. Naming the
                # values and their source makes the claim reviewable.
                "progress_transition": {
                    "type": ["object", "null"],
                    "properties": {
                        "field": {"type": "string"},
                        "before": {"type": "string"},
                        "after": {"type": "string"},
                        "cause": {"type": "string"},
                        "record": {
                            "type": "string",
                            "description": "One existing transition record file path relative to the Builder run directory, for example eval2/transition.json. No prose, backticks, directory paths, ranges, or lists of files. Put additional references in evidence.",
                        },
                    },
                    "required": ["field", "before", "after", "cause", "record"],
                    "additionalProperties": False,
                },
                "evidence": {"type": "string"},
            },
            "required": [
                "task_id",
                "design",
                "start",
                "progress",
                "terminal",
                "progress_transition",
                "evidence",
            ],
            "additionalProperties": False,
        },
    },
}


REPORT_CHECKS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "episode": {"type": "string"},
            "status": {"type": "string", "enum": ["fixed", "disproved", "unresolved"]},
            "observed": {"type": "string"},
            "record": {"type": "string"},
        },
        "required": ["episode", "status", "observed", "record"],
        "additionalProperties": False,
    },
}


def result_schema(phase: str, *, review_reports: bool = False) -> dict[str, Any]:
    """Return the minimal structured-output schema needed by one phase."""
    if phase not in PHASES:
        raise ValueError(f"unknown Builder phase: {phase}")
    properties = dict(COMMON_RESULT_PROPERTIES)
    required = list(COMMON_RESULT_PROPERTIES)
    if phase == "DISCOVER":
        properties.update(DISCOVERY_PROPERTIES)
        required.extend(DISCOVERY_PROPERTIES)
    elif phase == "EVALUATE":
        properties.update(EVALUATION_PROPERTIES)
        required.extend(EVALUATION_PROPERTIES)
        if review_reports:
            properties["report_checks"] = REPORT_CHECKS
            required.append("report_checks")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


PHASE_PROMPTS = {
    "DISCOVER": """DISCOVER only. Do not edit tracked files. Inspect the actual game,
source/package/network behavior, controls, progression, reset/start options, and
existing repository support. Use the run cache for bounded experiments. Identify
the engine and decide whether a robust offline environment with meaningful state
and short verifiable tasks is realistic. Return a concrete task_plan supported by
game or source evidence; the runner freezes it before implementation.
For a repair or observation request, existing task objectives remain binding.
Use the existing task prompt and original task plan; do not add a whole-card,
whole-level or whole-game requirement to a task that asks for a smaller milestone.
For example, delivering an exact ingredient count need not finish every recipe
check unless the existing task explicitly requires that.
Return unsuitable early when the environment or any meaningful task is not realistic.""",
    "IMPLEMENT": """IMPLEMENT only. Build the smallest robust environment within the
allowed paths. Package it for offline play, add meaningful state with honest
provenance, deterministic reset/start variants where feasible, natural tasks, and
an accurate concise agent guide in the game catalog. Set its game_name to this
run's exact game ID, including the numeric prefix, rather than the human title.
Do focused checks, but leave
independent end-to-end judgment to EVALUATE. In any new guide or task wording, tell
the player what to accomplish and the necessary controls or visible cues; do not
describe verifier implementation, partial-credit formulas or retained best progress.
Do not rewrite existing prompts solely for this writing rule.""",
    "EVALUATE": """EVALUATE only. Do not edit tracked files. First play every task
start from the players' side as the Player view section describes, and report it in
player_view. Then independently exercise offline loading with external network
blocked, real controls, changing state, reset/start variants, and every configured
task contract. For timing, follow harnesses/actions.py: a click, press_key or
press_keys without duration is a tap; specify duration to test a hold. Inspect concrete state
and frames, and verify the catalog guide against the actual controls and mechanics;
confirm init/reset enter the specified playable start, not just a clickable menu.
Use the configured task evaluator to check whether an intermediate state can
prematurely succeed; checking terminal alone misses score-threshold completion.
See the shared Builder workflow for the evaluator call and completion semantics.
Check the configured end field as well as the numeric target. An authored ending
can legitimately complete the stated objective below a numeric score guard; that
does not make the task impossible. Judge the existing objective, not a stricter
whole-card or whole-game goal, before proposing retirement.
API presence and a zero exit code are not evidence. Do not solve tasks merely to
pass evaluation. Once basic controls are confirmed, do not route-find or repeatedly
inspect frames to force a task event; prefer source-backed mappings and one bounded
debug trigger, then leave progress or terminal pending under the evidence rules in
the main Builder prompt.
Return passed when the environment works, every task start is verified, progress
and terminal contracts are verified or honestly pending, no task contract is
unsupported, and all claims are honest. A frozen task the game itself can never
complete goes in retired_tasks, as the main Builder prompt describes, and the pass
is judged on the remaining tasks. Return continue when a concrete bounded repair
remains, and unsuitable when further revision is not justified.""",
    "REVISE": """REVISE only. First diagnose the preceding evaluation from its state
stream, inputs, frames, browser logs, and relevant source. Then apply the smallest
bounded fix within the allowed paths. Do not broaden the architecture or modify
shared bases. Run focused checks, clean temporary processes, and leave formal
judgment to the next EVALUATE. Any new guide or task wording is for the player:
describe the goal and necessary controls or visible cues, not verifier implementation,
partial-credit formulas or retained best progress. Do not rewrite existing prompts
solely for this writing rule.""",
}


@dataclass(slots=True)
class BuilderState:
    game_id: str
    source_url: str = ""
    title: str = ""
    brief: str = ""
    player_reports: list[dict[str, Any]] = field(default_factory=list)
    phase: str = "DISCOVER"
    status: str = "running"
    engine: str = "unknown"
    thread_id: str | None = None
    revisions: int = 0
    turns: int = 0
    task_plan: list[dict[str, Any]] = field(default_factory=list)
    # Tasks removed from the frozen plan because the game cannot complete them, with
    # the reason and record the Builder gave; kept so the removal stays on record.
    retired_tasks: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    last_error: str | None = None
    # What the deterministic gate rejected last, included in the next turn.
    last_blocker: str | None = None

    @classmethod
    def load(
        cls,
        path: Path,
        game_id: str,
        source_url: str,
        title: str,
        brief: str,
        engine_hint: str,
    ) -> BuilderState:
        if not path.is_file():
            return cls(
                game_id=game_id,
                source_url=source_url,
                title=title,
                brief=brief,
                engine=engine_hint,
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("game_id") != game_id:
            raise ValueError("cached Builder game does not match")
        saved_url = str(raw.get("source_url") or "")
        if source_url and saved_url and source_url != saved_url:
            raise ValueError("source URL differs from the cached Builder run; use --fresh")
        saved_brief = str(raw.get("brief") or "")
        if brief and saved_brief and brief != saved_brief:
            raise ValueError("brief differs from the cached Builder run; use --fresh")
        saved_engine = str(raw.get("engine") or "unknown")
        if (
            engine_hint != "unknown"
            and saved_engine != "unknown"
            and engine_hint != saved_engine
        ):
            raise ValueError("engine hint differs from the cached Builder run; use --fresh")
        phase = str(raw.get("phase") or "DISCOVER")
        status = str(raw.get("status") or "running")
        if phase not in PHASES or status not in {"running", *TERMINAL_STATUSES}:
            raise ValueError("cached Builder state uses an obsolete format; use --fresh")
        if phase != "DISCOVER" and status == "running" and "task_plan" not in raw:
            raise ValueError("cached Builder state predates task planning; use --fresh")
        return cls(
            game_id=game_id,
            source_url=saved_url or source_url,
            title=str(raw.get("title") or title),
            brief=saved_brief or brief,
            player_reports=list(raw.get("player_reports") or []),
            phase=phase,
            status=status,
            engine=saved_engine if saved_engine != "unknown" else engine_hint,
            thread_id=raw.get("thread_id"),
            revisions=int(raw.get("revisions") or 0),
            turns=int(raw.get("turns") or 0),
            task_plan=list(raw.get("task_plan") or []),
            retired_tasks=list(raw.get("retired_tasks") or []),
            history=list(raw.get("history") or []),
            last_error=raw.get("last_error"),
            last_blocker=raw.get("last_blocker"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "game_id": self.game_id,
                    "source_url": self.source_url,
                    "title": self.title,
                    "brief": self.brief,
                    "player_reports": self.player_reports,
                    "phase": self.phase,
                    "status": self.status,
                    "engine": self.engine,
                    "thread_id": self.thread_id,
                    "revisions": self.revisions,
                    "turns": self.turns,
                    "task_plan": self.task_plan,
                    "retired_tasks": self.retired_tasks,
                    "history": self.history,
                    "last_error": self.last_error,
                    "last_blocker": self.last_blocker,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


class Builder:
    """Drive one game through a bounded build/evaluate/revise loop."""

    def __init__(
        self,
        game_id: str,
        *,
        source_url: str = "",
        title: str = "",
        brief: str = "",
        player_reports: list[dict[str, Any]] | None = None,
        engine: str = "unknown",
        redesign_tasks: bool = False,
        task_spec: Path | None = None,
        root: Path | None = None,
        agent: AgentProfile,
        max_revisions: int = 3,
        turn_timeout_s: float = 90 * 60,
        full_access: bool = False,
        environment: Mapping[str, str] | None = None,
        fresh: bool = False,
        run_id: str | None = None,
    ):
        self.root = (root or Path(__file__).resolve().parents[2]).resolve()
        self.game_id = _game_id(game_id)
        if engine not in set(ENGINES):
            raise ValueError(f"invalid engine hint: {engine!r}")
        if max_revisions < 0:
            raise ValueError("max_revisions must be non-negative")
        self.max_revisions = max_revisions
        self.redesign_tasks = bool(redesign_tasks)
        self.task_spec_path = task_spec.resolve() if task_spec is not None else None
        self.task_spec = (load_specification(self.task_spec_path, self.game_id)
                          if self.task_spec_path is not None else None)
        if self.task_spec is not None:
            if self.redesign_tasks:
                raise ValueError("Published benchmark tasks cannot be redesigned")
            prepare_specification(self.root, self.task_spec)
        run_id = run_id.strip() if run_id is not None else None
        if run_id is not None:
            allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
            if (
                not run_id
                or Path(run_id).name != run_id
                or any(character not in allowed for character in run_id)
            ):
                raise ValueError(
                    "run_id may contain only lowercase letters, digits, '-' and '_'"
                )
        self.run_dir = self.root / ".cache" / "environment-factory" / "builder" / self.game_id
        if run_id is not None:
            self.run_dir /= run_id
        if fresh and self.run_dir.exists():
            shutil.rmtree(self.run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.run_dir / "state.json"
        self.state = BuilderState.load(
            self.state_path,
            self.game_id,
            source_url.strip(),
            title.strip(),
            brief.strip(),
            engine,
        )
        if player_reports is not None:
            self.state.player_reports = list(player_reports)
        self.session = None
        if self.state.status not in TERMINAL_STATUSES:
            self.session = create_coding_session(
                agent=agent,
                root=self.root,
                run_dir=self.run_dir,
                thread_id=self.state.thread_id,
                turn_timeout_s=turn_timeout_s,
                full_access=full_access,
                environment=environment,
                on_thread_started=self._remember_thread,
            )

    def run(self) -> BuilderState:
        if self.state.status in TERMINAL_STATUSES:
            self._cleanup_terminal_cache()
            return self.state
        while self.state.status == "running":
            phase = self.state.phase
            print(f"[{self.game_id}] {phase} (turn {self.state.turns + 1})", flush=True)
            try:
                result = self._run_phase_turn(phase)
            except (OSError, RuntimeError, TimeoutError) as exc:
                self.state.last_error = str(exc)
                self.state.save(self.state_path)
                raise
            self._accept_result(result)
        self._cleanup_terminal_cache()
        return self.state

    def _run_phase_turn(self, phase: str) -> dict[str, Any]:
        prompt = self._prompt(phase)
        last_error: Exception | None = None
        for attempt in range(MAX_PROTOCOL_REPAIRS + 1):
            try:
                result = self.session.turn(
                    prompt,
                    result_schema(phase, review_reports=bool(self.state.player_reports)),
                    sandbox="workspace-write",
                )
                result = self._apply_acceptance_gate(result, phase)
                self._validate_result(result, phase)
                return result
            except (BuilderProtocolError, CodingProtocolError) as exc:
                last_error = exc
                if attempt >= MAX_PROTOCOL_REPAIRS or not self.session.thread_id:
                    break
                prompt = (
                    "The runner rejected your previous response before advancing the "
                    f"{phase} phase:\n\n{exc}\n\n"
                    "Correct this contract violation in the same phase and return a new "
                    "structured result. Preserve the frozen task semantics and do not "
                    "repeat unrelated work."
                )
        assert last_error is not None
        raise last_error

    def _prompt(self, phase: str) -> str:
        instruction = self._player_report_notice() + PHASE_PROMPTS[phase]
        if self.task_spec_path is not None:
            instruction += (
                f"\nRead the published specification at `{self.task_spec_path}`. "
                "It supplies the game settings and complete task set. In DISCOVER, "
                "inspect how to implement these tasks instead of inventing new ones. "
                "Keep their exact prompts, starts, score definitions, targets, controls "
                "and difficulty labels. Implement the required URL variants and state "
                "fields in the game. Do not edit the published specification, weaken "
                "a task or retire any task. If reconstruction is impossible, return "
                "unsuitable with the concrete reason.\n"
            )
        if phase == "EVALUATE" and self.state.revisions >= self.max_revisions:
            instruction += (
                "\nThis is the final allowed evaluation. Do not return continue: "
                "classify the result honestly as passed or unsuitable."
            )
        skill = ENGINE_SKILLS.get(self.state.engine)
        if skill:
            skill_line = (
                f"Use the repository skills {BUILDER_SKILL} and {skill} for this phase."
            )
        else:
            # Naming the engines that have one matters: the old wording told the
            # agent to load "the matching engine skill" for engines that have none,
            # which is an instruction to load nothing.
            covered = ", ".join(sorted(ENGINE_SKILLS))
            skill_line = (
                f"Use the repository skill {BUILDER_SKILL} for this phase. Identify "
                "the engine near the start of DISCOVER; if it turns out to be one of "
                f"{covered}, also load that engine's skill during this same phase, "
                "before any engine-specific reverse engineering. For any other "
                f"engine {BUILDER_SKILL} is the only one that applies."
            )
        if self.state.thread_id:
            scope = (
                f"\nBuild brief (still binding):\n{self.state.brief}\n"
                if self.state.brief
                else ""
            )
            plan = self._frozen_task_plan(phase)
            return (
                f"Continue the same game and conversation.\n"
                f"Current game id: `{self.game_id}`. Working directory: `{self.run_dir}`.\n"
                f"Runtime: `games/benchmark/{self.game_id}/`; "
                f"catalog: `catalog/games/{self.game_id}.yaml`; "
                f"tasks: `catalog/tasks/{self.game_id}/`.\n{skill_line}{scope}\n"
                f"{self._redesign_notice()}{self._blocker_notice()}"
                f"{self._evaluation_notice(phase)}{plan}{instruction}"
            )
        template = (Path(__file__).with_name("prompts") / "builder.md").read_text(
            encoding="utf-8"
        )
        return template.format(
            game_id=self.game_id,
            title=self.state.title or "(not supplied)",
            source_url=self.state.source_url or "(inspect the existing local game)",
            run_dir=self.run_dir,
            brief=self.state.brief or "(no narrower build brief supplied)",
            skill_instruction=skill_line,
            instruction=(
                f"{self._redesign_notice()}{self._evaluation_notice(phase)}"
                f"{self._frozen_task_plan(phase)}{instruction}"
            ),
        )

    def _player_report_notice(self) -> str:
        if not self.state.player_reports:
            return ""
        reports = json.dumps(self.state.player_reports, ensure_ascii=False, indent=2)
        return (
            f"Player reports to check (suspicions, not verified defects):\n{reports}\n\n"
            "In EVALUATE, return one report_checks item for each reported episode. "
            "Use fixed only after finding a defect, repairing it and checking the "
            "affected behavior through real player inputs. Use disproved only when "
            "a concrete player-side check explains or contradicts the reported "
            "failure, including the relevant gameplay prerequisites. Record the "
            "inputs and observed response in observed and cite one file relative "
            "to this run directory in record. Debug writes or directly triggering "
            "completion do not verify the affected player interaction. You need not "
            "solve the whole task to check a specific control or observation. "
            "Use unresolved when neither conclusion is supported; explain what "
            "remains unknown, and record may be empty. Merely failing to reproduce "
            "a report does not disprove it. A known defect still present needs "
            "continue or unsuitable, not a passed environment with an unresolved "
            "report. EVALUATE verifies the specific repair; the player does not "
            "have to learn to solve the task before a report can be closed. "
            "For source=system reports, reproduce the exact task and seed through "
            "BrowserGameManager. An init/reset failure can occur before any player "
            "action; verify that operation with wait_actionable=True. Do not change "
            "framework timeouts, the seed, or the task to hide the failure. The runner "
            "will independently check initialization and reset at the reported seed.\n\n"
        )

    def _evaluation_notice(self, phase: str) -> str:
        if phase != "REVISE":
            return ""
        evaluation = next(
            (item for item in reversed(self.state.history) if item.get("phase") == "EVALUATE"),
            None,
        )
        if evaluation is None:
            return ""
        # An operator can accept an evaluation outside the coding session. Its
        # conversation then lacks the result even though persistent state has
        # advanced to REVISE. Carry the actual result into the resumed prompt.
        details = {key: evaluation.get(key) for key in ("summary", "blocker", "evidence")}
        return ("Latest accepted EVALUATE result (including any operator review):\n"
                + json.dumps(details, ensure_ascii=False) + "\n\n")

    def _redesign_notice(self) -> str:
        if not self.redesign_tasks:
            return ""
        return (
            "This run is an audit of an existing environment, and its task set is "
            "NOT frozen. The tasks now in `catalog/tasks/` were written before the "
            "current task-design rules and may be wrong: read the game first and "
            "decide what its natural task families are, then compare. Keep a task "
            "that is already right, fix one whose threshold or signal is wrong, "
            "replace a set that measures one thing five times, and add a family the "
            "game supports and the set is missing. Name every task you drop in "
            "evidence, with what replaced it or why the game cannot support it. "
            "Renumber the survivors so the ids stay contiguous. Leave working "
            "instrumentation alone: rebuild `game_api.js` only where it is actually "
            "wrong, and say what was wrong with it.\n\n"
        )

    def _blocker_notice(self) -> str:
        if not self.state.last_blocker:
            return ""
        return (
            "The runner's deterministic gate rejected the previous evaluation for "
            f"this reason:\n{self.state.last_blocker}\n\n"
            "Address exactly this before claiming a pass again.\n\n"
        )

    def _remember_thread(self, thread_id: str) -> None:
        self.state.thread_id = thread_id
        self.state.save(self.state_path)

    def _validate_result(self, result: Any, phase: str) -> None:
        if not isinstance(result, dict):
            raise BuilderProtocolError("Builder final response must be a JSON object")
        if result.get("phase") != phase:
            raise BuilderProtocolError(
                f"Builder returned phase {result.get('phase')!r}, expected {phase!r}"
            )
        status = result.get("status")
        if status not in {"continue", *TERMINAL_STATUSES}:
            raise BuilderProtocolError(f"invalid Builder status: {status!r}")
        if status == "passed" and phase != "EVALUATE":
            raise BuilderProtocolError("only EVALUATE may pass an environment")
        if (
            phase == "EVALUATE"
            and self.state.revisions >= self.max_revisions
            and status == "continue"
        ):
            raise BuilderProtocolError(
                "final EVALUATE must classify the environment instead of continuing"
            )
        if result.get("engine") not in set(ENGINES):
            raise BuilderProtocolError("Builder returned an invalid engine")
        if (
            phase == "DISCOVER"
            and status == "continue"
            and result.get("engine") == "unknown"
            and self.state.engine == "unknown"
        ):
            raise BuilderProtocolError("Builder must identify the engine before IMPLEMENT")
        if phase == "DISCOVER":
            self._validate_task_plan(result, status)
        elif phase == "EVALUATE" and self.state.player_reports:
            self._validate_report_checks(result.get("report_checks"))
        elif phase != "EVALUATE" and status in {"continue", "passed"}:
            # Let an artifact-gate rejection reach REVISE instead of trapping
            # EVALUATE in protocol repair over the same invalid task files.
            self._validate_configured_task_ids()

    def _validate_report_checks(self, checks: Any) -> None:
        expected = {str(item["episode"]) for item in self.state.player_reports}
        if not isinstance(checks, list):
            raise BuilderProtocolError("EVALUATE must check each player report in report_checks")
        seen = set()
        for item in checks:
            if not isinstance(item, dict):
                raise BuilderProtocolError("each report check must be an object")
            episode = item.get("episode")
            if episode not in expected or episode in seen:
                raise BuilderProtocolError("report_checks must name every reported episode exactly once")
            seen.add(episode)
            if item.get("status") not in {"fixed", "disproved", "unresolved"}:
                raise BuilderProtocolError("invalid player report check status")
            if not str(item.get("observed") or "").strip():
                raise BuilderProtocolError("each report check must explain the observed behavior")
            if item["status"] != "unresolved" and not self._resolves_in_run_dir(str(item.get("record") or "")):
                raise BuilderProtocolError("a fixed or disproved report needs a record file in the run directory")
        if seen != expected:
            raise BuilderProtocolError("report_checks must name every reported episode exactly once")

    def _validate_task_plan(self, result: dict[str, Any], status: str) -> None:
        plan = result.get("task_plan")
        if not isinstance(plan, list):
            raise BuilderProtocolError("DISCOVER must return task_plan")
        if status == "continue" and not plan:
            raise BuilderProtocolError("a buildable environment needs at least one planned task")
        ids = [
            str(item.get("task_id") or "").strip()
            for item in plan
            if isinstance(item, dict)
        ]
        if len(ids) != len(plan) or any(not task_id for task_id in ids):
            raise BuilderProtocolError("every planned task needs a non-empty task_id")
        duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
        if duplicates:
            raise BuilderProtocolError(f"duplicate planned task ids: {', '.join(duplicates)}")
        prefix = expected_task_id_prefix(self.game_id)
        off_convention = [
            task_id
            for task_id in ids
            if not task_id.startswith(prefix) or not TASK_ID_PATTERN.fullmatch(task_id)
        ]
        if off_convention:
            raise BuilderProtocolError(
                f"task ids must be {prefix}<NN>, numbered within this game: "
                + ", ".join(off_convention)
            )
        for item, task_id in zip(plan, ids):
            signal = item.get("progress_signal") if isinstance(item, dict) else None
            if not isinstance(signal, dict):
                raise BuilderProtocolError("every planned task needs a progress_signal")
            field_name = str(signal.get("field") or "").strip()
            if not field_name:
                raise BuilderProtocolError(
                    "every planned progress signal needs a non-empty field"
                )
            granularity = str(signal.get("granularity") or "")
            if granularity in {"counter", "flag"} and not str(
                signal.get("coarse_reason") or ""
            ).strip():
                raise BuilderProtocolError(
                    f"{task_id} measures {field_name!r} as a {granularity}; explain "
                    "why this measure represents the task in progress_signal.coarse_reason."
                )

        configured = (sorted(task["task_id"] for task in self.task_spec["tasks"])
                      if self.task_spec is not None else self._configured_task_ids())
        if configured and sorted(ids) != configured:
            missing = sorted(set(configured) - set(ids))
            extra = sorted(set(ids) - set(configured))
            if self.redesign_tasks:
                # An audit exists to fix task sets that were never designed, so
                # replacing them is the point. Dropping one still has to be argued:
                # the evidence is the only record of why a task the corpus had is
                # gone, and without it a redesign is indistinguishable from losing
                # coverage.
                if missing and not self._evidence_mentions(result, missing):
                    raise BuilderProtocolError(
                        "a redesign that drops "
                        + ", ".join(missing)
                        + " must name each dropped task in evidence and say what "
                        "replaced it or why the game does not support it"
                    )
            else:
                detail = []
                if missing:
                    detail.append(f"missing {', '.join(missing)}")
                if extra:
                    detail.append(f"added {', '.join(extra)}")
                raise BuilderProtocolError(
                    "DISCOVER must preserve the existing task set ("
                    + "; ".join(detail)
                    + ")"
                )

    @staticmethod
    def _evidence_mentions(result: dict[str, Any], task_ids: list[str]) -> bool:
        text = " ".join(
            str(item) for item in (result.get("evidence") or [])
        ) + " " + str(result.get("summary") or "")
        return all(task_id in text for task_id in task_ids)

    def _configured_task_ids(self) -> list[str]:
        task_dir = self.root / "catalog" / "tasks" / self.game_id
        return sorted(path.stem for path in task_dir.glob("*.yaml"))

    def _validate_configured_task_ids(self) -> None:
        planned = sorted(str(item["task_id"]) for item in self.state.task_plan)
        configured = self._configured_task_ids()
        if configured != planned:
            raise BuilderProtocolError(
                "configured task ids differ from the frozen task plan: "
                f"planned={planned}, configured={configured}"
            )

    def _frozen_task_plan(self, phase: str) -> str:
        if phase == "DISCOVER":
            return ""
        if not self.state.task_plan:
            raise RuntimeError("Builder has no frozen task plan; use --fresh")
        encoded = json.dumps(self.state.task_plan, ensure_ascii=False, indent=2)
        if self.task_spec is not None:
            repair_rules = (
                "Repair the runtime without changing the published catalog settings. "
                "Published benchmark tasks may not be retired. Return unsuitable if "
                "their contract cannot be implemented honestly.\n\n"
            )
        else:
            repair_rules = (
                "You may repair field names, verifier wiring, step budgets, and prompt "
                "wording only when the frozen semantics stay exactly the same. "
                "If the game itself can never complete a task, or its contract cannot "
                "be instrumented honestly, retire it in EVALUATE with its evidence "
                "instead of weakening it.\n\n"
            )
        return (
            "Frozen task plan (binding):\n"
            f"{encoded}\n\n"
            "Do not alter its task ids, objective, start semantics, progress meaning, "
            "success condition, or difficulty tier to make implementation or "
            "evaluation easier. " + repair_rules
        )

    def _apply_acceptance_gate(
        self, result: dict[str, Any], phase: str
    ) -> dict[str, Any]:
        """Reject incomplete passes and keep bounded repairs inside the loop."""
        if phase != "EVALUATE" or result.get("status") != "passed":
            return result

        configured = self._configured_task_ids()
        task_contracts = result.get("task_contracts")
        reported = (
            [
                str(item.get("task_id"))
                for item in task_contracts
                if isinstance(item, dict) and item.get("task_id")
            ]
            if isinstance(task_contracts, list)
            else []
        )
        duplicate_ids = sorted(
            {task_id for task_id in reported if reported.count(task_id) > 1}
        )
        by_id = (
            {
                str(item.get("task_id")): item
                for item in task_contracts
                if isinstance(item, dict) and item.get("task_id")
            }
            if isinstance(task_contracts, list)
            else {}
        )
        # A retired task is judged by its evidence, not its contract, so the checks
        # below cover the tasks that remain.
        retired, retirement_reasons = self._retirement(result.get("retired_tasks"))
        configured = [task_id for task_id in configured if task_id not in retired]
        by_id = {task_id: item for task_id, item in by_id.items() if task_id not in retired}

        reasons: list[str] = list(retirement_reasons)
        if self.task_spec is not None:
            reasons.extend(specification_errors(self.root, self.task_spec))
        signals = result.get("state_signals")
        if not isinstance(signals, list) or not any(
            isinstance(signal, dict)
            and signal.get("provenance") in NONVISUAL_PROVENANCES
            for signal in signals
        ):
            reasons.append("no meaningful non-visual state signal was verified")
        if not configured:
            reasons.append("no configured task exists")
        missing = sorted(set(configured) - set(by_id))
        extra = sorted(set(by_id) - set(configured))
        if missing:
            reasons.append(f"missing task results: {', '.join(missing)}")
        if extra:
            reasons.append(f"results for unconfigured tasks: {', '.join(extra)}")
        if duplicate_ids:
            reasons.append(f"duplicate task results: {', '.join(duplicate_ids)}")
        invalid_contracts = [
            f"{task_id}.{field}"
            for task_id in configured
            if task_id in by_id
            for field in ("design", "start", "progress", "terminal")
            if (
                by_id[task_id].get(field)
                not in (
                    {"verified", "unsupported"}
                    if field == "design"
                    else TASK_CONTRACT_STATUSES
                )
            )
        ]
        if invalid_contracts:
            reasons.append(f"invalid task contracts: {', '.join(invalid_contracts)}")
        unverified_designs = [
            task_id
            for task_id in configured
            if task_id in by_id and by_id[task_id].get("design") != "verified"
        ]
        if unverified_designs:
            reasons.append(
                "tasks drifted from the frozen plan: " + ", ".join(unverified_designs)
            )
        unverified_starts = [
            task_id
            for task_id in configured
            if task_id in by_id and by_id[task_id].get("start") != "verified"
        ]
        if unverified_starts:
            reasons.append(f"task starts not verified: {', '.join(unverified_starts)}")
        unsupported_progress = [
            task_id
            for task_id in configured
            if task_id in by_id and by_id[task_id].get("progress") == "unsupported"
        ]
        if unsupported_progress:
            reasons.append(
                f"task progress contracts unsupported: {', '.join(unsupported_progress)}"
            )
        unsupported_terminal = [
            task_id
            for task_id in configured
            if task_id in by_id and by_id[task_id].get("terminal") == "unsupported"
        ]
        if unsupported_terminal:
            reasons.append(
                f"task terminal contracts unsupported: {', '.join(unsupported_terminal)}"
            )
        reasons.extend(self._transition_reasons(configured, by_id))
        reasons.extend(self._player_view_reasons(result.get("player_view")))
        gate_evidence: list[str] = []
        if not reasons:
            gate_evidence = self._retire(retired)
            interface_reports = [item for item in self.state.player_reports if item.get("source") == "system"]
            gate = validate_artifact(self.root, self.game_id, self.state.task_plan,
                                     **({"interface_reports": interface_reports} if interface_reports else {}))
            gate_evidence += gate.evidence
            reasons.extend(gate.errors)
        if not reasons:
            self.state.last_blocker = None
            accepted = dict(result)
            accepted["evidence"] = [
                *list(result.get("evidence") or []),
                *[f"Deterministic artifact gate: {item}." for item in gate_evidence],
            ]
            return accepted

        gated = dict(result)
        reason = "; ".join(reasons)
        self.state.last_blocker = reason
        # With no revision left a rejected pass is final: the environment did not
        # reach the bar.
        exhausted = self.state.revisions >= self.max_revisions
        gated["status"] = "unsuitable" if exhausted else "continue"
        gated["blocker"] = reason
        gated["summary"] = (f"Runner rejected the pass claim after the final revision: {reason}."
                            if exhausted else f"Runner rejected the pass claim: {reason}.")
        gated["evidence"] = [
            *list(result.get("evidence") or []),
            f"Deterministic acceptance gate: {reason}.",
        ]
        return gated

    def _retirement(self, raw: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """The newly retired tasks that hold up, and why any others do not: a retired
        task must be in the frozen plan, give a reason and cite a record under the run
        directory, and at most a third of the original plan may be retired."""
        if self.task_spec is not None and raw:
            return {}, ["Published benchmark tasks may not be retired"]
        earlier = {str(item.get("task_id")) for item in self.state.retired_tasks}
        planned = {str(item.get("task_id")) for item in self.state.task_plan if isinstance(item, dict)}
        retired: dict[str, dict[str, Any]] = {}
        reasons: list[str] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or "")
            record = str(item.get("record") or "")
            if task_id in earlier:
                continue
            if task_id not in planned:
                reasons.append(f"retired task {task_id!r} is not in the frozen plan")
            elif task_id in retired:
                reasons.append(f"{task_id} is retired twice")
            elif not str(item.get("reason") or "").strip():
                reasons.append(f"retired task {task_id} gives no reason")
            elif not record or not self._resolves_in_run_dir(record):
                reasons.append(
                    f"retired task {task_id} cites record {record!r}, which is not one file "
                    f"path relative to {self.run_dir}"
                )
            else:
                retired[task_id] = item
        if (len(earlier) + len(retired)) * 3 > len(planned) + len(earlier):
            reasons.append(
                f"{len(earlier) + len(retired)} of {len(planned) + len(earlier)} planned "
                "tasks would be retired; at most a third may be"
            )
        return retired, reasons

    def _retire(self, retired: dict[str, dict[str, Any]]) -> list[str]:
        """Remove retired tasks from the catalog and the frozen plan, keeping the record."""
        task_dir = self.root / "catalog" / "tasks" / self.game_id
        notes = []
        for task_id, item in retired.items():
            (task_dir / f"{task_id}.yaml").unlink(missing_ok=True)
            entry = {"task_id": task_id, "reason": str(item["reason"]), "record": str(item["record"])}
            self.state.retired_tasks.append(entry)
            notes.append(f"retired {task_id}: {entry['reason']} (record {entry['record']})")
        if retired:
            self.state.task_plan = [
                item for item in self.state.task_plan if str(item.get("task_id")) not in retired
            ]
            self.state.save(self.state_path)
        return notes

    def _transition_reasons(
        self, configured: list[str], by_id: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Require a reviewable observation behind every verified progress claim."""
        planned_fields = {
            str(item.get("task_id")): str(
                (item.get("progress_signal") or {}).get("field") or ""
            )
            for item in self.state.task_plan
            if isinstance(item, dict)
        }
        reasons: list[str] = []
        for task_id in configured:
            contract = by_id.get(task_id)
            if not contract or contract.get("progress") != "verified":
                continue
            transition = contract.get("progress_transition")
            if not isinstance(transition, dict):
                reasons.append(
                    f"{task_id} claims verified progress without a progress_transition"
                )
                continue
            field_name = str(transition.get("field") or "")
            if field_name != planned_fields.get(task_id):
                reasons.append(
                    f"{task_id} observed {field_name!r}, not its frozen progress signal "
                    f"{planned_fields.get(task_id)!r}"
                )
            if str(transition.get("before")) == str(transition.get("after")):
                reasons.append(f"{task_id} recorded a transition that did not move")
            record = str(transition.get("record") or "")
            if not record or not self._resolves_in_run_dir(record):
                reasons.append(
                    f"{task_id} cites transition record {record!r}, which is not one file "
                    f"path relative to {self.run_dir}"
                )
        return reasons

    def _player_view_reasons(self, view: Any) -> list[str]:
        """A pass needs the players' side checked: the screen and each guide control
        verified, each with a record under the run directory."""
        if not isinstance(view, dict):
            return ["the players' side was not checked (no player_view)"]
        reasons: list[str] = []
        if view.get("screen") != "verified":
            reasons.append("the screenshot players get shows a defect")
        record = str(view.get("screen_record") or "")
        if not record or not self._resolves_in_run_dir(record):
            reasons.append(f"player screen record {record!r} is not one file path relative to {self.run_dir}")
        controls = [item for item in view.get("controls") or [] if isinstance(item, dict)]
        if not controls:
            reasons.append("no guide control was checked from the players' side")
        for item in controls:
            name = str(item.get("control") or "")
            if item.get("status") != "verified":
                reasons.append(f"guide control {name!r} does not do what the guide says when sent as players send it")
            record = str(item.get("record") or "")
            if not record or not self._resolves_in_run_dir(record):
                reasons.append(f"guide control {name!r} cites record {record!r}, which is not one file path relative to {self.run_dir}")
        return reasons

    def _resolves_in_run_dir(self, record: str) -> bool:
        try:
            resolved = (self.run_dir / record).resolve()
            resolved.relative_to(self.run_dir)
            # A record that is prose rather than a path can be too long for the OS.
            return resolved.is_file()
        except (OSError, ValueError):
            return False

    def _accept_result(self, result: dict[str, Any]) -> None:
        phase = self.state.phase
        status = str(result["status"])
        engine = str(result["engine"])
        if self.state.engine != "unknown" and engine not in {self.state.engine, "unknown"}:
            self.state.last_error = "engine changed after discovery"
            self.state.save(self.state_path)
            raise RuntimeError(self.state.last_error)
        if engine != "unknown":
            self.state.engine = engine
        self.state.turns += 1
        self.state.history.append(result)
        self.state.last_error = None

        if status in TERMINAL_STATUSES:
            self.state.status = status
        elif phase == "DISCOVER":
            self.state.task_plan = list(result["task_plan"])
            self.state.phase = "IMPLEMENT"
        elif phase == "IMPLEMENT":
            self.state.phase = "EVALUATE"
        elif phase == "EVALUATE":
            self.state.phase = "REVISE"
        elif phase == "REVISE":
            self.state.revisions += 1
            self.state.phase = "EVALUATE"
        self.state.save(self.state_path)
        print(f"[{self.game_id}] {result['summary']}", flush=True)

    def _cleanup_terminal_cache(self) -> None:
        """Discard duplicate/resumability-only artifacts after a terminal result."""
        if self.state.status not in TERMINAL_STATUSES:
            return
        # Keep agent session logs and agent_backend.json for reviewing completed runs.
        for name in (
            "result.schema.json",
            "last_response.json",
        ):
            (self.run_dir / name).unlink(missing_ok=True)
        shutil.rmtree(self.run_dir / "codex_home", ignore_errors=True)


def _game_id(value: str) -> str:
    value = value.strip()
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
    if not value or Path(value).name != value or any(c not in allowed for c in value):
        raise ValueError("game id may contain only lowercase letters, digits, '-' and '_'")
    return value


__all__ = ["Builder", "BuilderState", "PHASES", "result_schema"]
