"""Deterministic catalog and runtime checks for one Builder artifact."""

from __future__ import annotations

import asyncio
import math
import posixpath
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from catalog._yaml import load_yaml_mapping
from catalog.games._base import GameDefinition
from catalog.tasks._base import TaskSpec
from env import BrowserConfig, BrowserGameManager, GameLauncher
from env.browser_manager import COLD_START_TIMEOUT_S, RESET_TIMEOUT_S
from env.game_launcher import append_url_suffix
from tools.paths import temporary_dir
from tools.unity_webgl.profile import load_profile_for_game


# Ceiling on one variant's whole browser session. The individual waits inside it
# already have their own deadlines, but only the ones that poll: launching the
# browser, reading state and closing are plain awaits on the Playwright transport,
# and a browser that dies without the driver reporting it leaves those waiting for a
# message that is never coming. One game hung a whole audit pass for ninety minutes
# that way, silently, because nothing above this had a deadline either. Generous
# enough to cover a cold start plus two resets and still bound the pass.
VARIANT_TIMEOUT_S = COLD_START_TIMEOUT_S + 2 * RESET_TIMEOUT_S + 120.0
# A browser that is already unresponsive will not shut down politely either.
CLOSE_TIMEOUT_S = 30.0

REQUIRED_STATE_FIELDS = {
    "schemaVersion",
    "gameId",
    "timestampMs",
    "gameTimeMs",
    "status",
    "is_actionable",
    "terminal",
    "game_state",
    "metrics",
}


@dataclass(slots=True)
class ArtifactGateReport:
    errors: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors


def validate_artifact(
    root: Path,
    game_id: str,
    task_plan: list[dict[str, Any]],
    *,
    interface_reports: list[dict[str, Any]] | None = None,
) -> ArtifactGateReport:
    """Load the real catalog contract, then exercise it through the main browser stack."""
    report = ArtifactGateReport()
    try:
        game, tasks = _validate_catalog(root, game_id, task_plan, report)
    except (OSError, TypeError, ValueError) as exc:
        report.errors.append(f"catalog validation failed: {exc}")
        return report
    if report.errors:
        return report

    try:
        if interface_reports:
            asyncio.run(_validate_reported_interfaces(root, game_id, game, tasks, interface_reports, report))
        if report.errors:
            return report
        asyncio.run(_validate_runtime(root, game_id, game, tasks, task_plan, report))
    except Exception as exc:  # noqa: BLE001 - gate failures become Builder evidence
        report.errors.append(f"runtime validation failed: {exc}")
    return report


async def _validate_reported_interfaces(root, game_id, game, tasks, reports, result):
    """Independently check the reported task/seed before accepting a repair."""
    by_id = {task.task_id: task for task in tasks}
    cases = {(item["task"], int(item["seed"])) for item in reports}
    launcher = GameLauncher(game_name=game_id, base_dir=root / "games" / "benchmark")
    try:
        url = launcher.start()
        for task_id, seed in sorted(cases):
            if task_id not in by_id:
                result.errors.append(f"reported task {task_id} was removed instead of repaired")
                continue
            with tempfile.TemporaryDirectory(prefix="reported_interface_", dir=temporary_dir("builder")) as temporary:
                manager = BrowserGameManager(BrowserConfig(
                    game_url=append_url_suffix(url, by_id[task_id].game_url_suffix),
                    width=game.width, height=game.height, headless=True,
                    speed_multiplier=game.speed_multiplier, random_seed=seed,
                    readiness_timeout_s=game.readiness_timeout_s,
                    screenshot_dir=Path(temporary), block_external_network=True,
                    unity_profile=load_profile_for_game(game_id, root / "catalog" / "unity"),
                ))
                try:
                    await manager.start(wait_actionable=True)
                    await manager.reset_game(wait_actionable=True)
                    result.evidence.append(f"reported {task_id}, seed {seed}: init and reset became actionable")
                except Exception as exc:
                    result.errors.append(f"reported {task_id}, seed {seed}: {exc}")
                finally:
                    await manager.close()
            if result.errors:
                break
    finally:
        launcher.stop()


def _validate_catalog(
    root: Path,
    game_id: str,
    task_plan: list[dict[str, Any]],
    report: ArtifactGateReport,
) -> tuple[GameDefinition, list[TaskSpec]]:
    game_path = root / "catalog" / "games" / f"{game_id}.yaml"
    raw_game = load_yaml_mapping(game_path)
    game = GameDefinition.from_mapping(raw_game)
    if game.game_name != game_id:
        report.errors.append(
            f"catalog game_name is {game.game_name!r}, expected {game_id!r}"
        )

    raw_roles = raw_game.get("game_roles")
    if not isinstance(raw_roles, list) or len(raw_roles) != 1:
        report.errors.append("catalog must contain exactly one game role")
    else:
        role = raw_roles[0]
        if not isinstance(role, dict) or not isinstance(role.get("controls"), dict):
            report.errors.append("game role must contain role-level controls")
        context = role.get("context") if isinstance(role, dict) else None
        if isinstance(context, dict) and "controls" in context:
            report.errors.append("controls is nested under context instead of the game role")

    game_dir = root / "games" / "benchmark" / game_id
    if not game_dir.is_dir():
        report.errors.append(f"runtime directory is missing: {game_dir}")
    else:
        _validate_runtime_layout(game_dir, report)

    planned = {
        str(item.get("task_id")): item
        for item in task_plan
        if isinstance(item, dict) and item.get("task_id")
    }
    task_dir = root / "catalog" / "tasks" / game_id
    paths = sorted(task_dir.glob("*.yaml"))
    if sorted(path.stem for path in paths) != sorted(planned):
        report.errors.append("task files differ from the frozen task plan")

    tasks: list[TaskSpec] = []
    for path in paths:
        raw_task = load_yaml_mapping(path)
        task = TaskSpec.from_mapping(raw_task)
        tasks.append(task)
        if task.task_id != path.stem or task.game_id != game_id:
            report.errors.append(f"task identity mismatch in {path.name}")
        planned_task = planned.get(task.task_id, {})
        if task.evaluator_id != "game_api_metric":
            report.errors.append(f"{task.task_id} must use game_api_metric")
        if task.task_target_score_field is None:
            report.errors.append(f"{task.task_id} has no numeric target score")
        elif not all(math.isfinite(value) for value in (
            task.task_start_score_field, task.task_target_score_field
        )):
            report.errors.append(f"{task.task_id} start and target scores must be finite")
        elif task.task_target_score_field <= task.task_start_score_field:
            report.errors.append(
                f"{task.task_id} target score must be greater than its start score"
            )
        config = task.evaluator_config
        score_field = config.get("score_field")
        aggregate = config.get("aggregate_score_fields")
        score_sources: list[str] = []
        if isinstance(score_field, str) and score_field:
            score_sources.append(score_field)
        if isinstance(aggregate, list):
            score_sources.extend(
                value for value in aggregate if isinstance(value, str) and value
            )
        if not score_sources:
            report.errors.append(f"{task.task_id} has no evaluator score source")
        planned_signal = planned_task.get("progress_signal")
        planned_field = (
            planned_signal.get("field") if isinstance(planned_signal, dict) else None
        )
        if planned_field not in score_sources:
            report.errors.append(
                f"{task.task_id} evaluator does not use its frozen progress signal"
            )

    if not report.errors:
        report.evidence.append(
            f"catalog loaded exactly one role and {len(tasks)} frozen tasks"
        )
    return game, tasks


# Keep the instrumentation in one readable file so later repairs do not require
# rebuilding a bundle from temporary source files.
_API_FILE = "game_api.js"
_SCRIPT_SRC = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)


def _local_script_path(source: str) -> str | None:
    try:
        reference = urlsplit(source)
    except ValueError:
        return None
    if reference.scheme or reference.netloc:
        return None
    return posixpath.normpath(reference.path.lstrip("/"))


def _validate_runtime_layout(game_dir: Path, report: ArtifactGateReport) -> None:
    index = game_dir / "index.html"
    if not index.is_file():
        report.errors.append("runtime is missing index.html")
        return
    if not (game_dir / _API_FILE).is_file():
        report.errors.append(
            f"runtime is missing {_API_FILE}; the game API must stay one readable "
            "file, not compiled into a bundle"
        )
        return
    try:
        markup = index.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.errors.append(f"cannot read index.html: {exc}")
        return
    sources = {_local_script_path(source) for source in _SCRIPT_SRC.findall(markup)}
    if _API_FILE not in sources:
        report.errors.append(f"index.html has no <script src> loading {_API_FILE}")
        return
    report.evidence.append(f"index.html loads {_API_FILE} as a top-level script")


async def _validate_runtime(
    root: Path,
    game_id: str,
    game: GameDefinition,
    tasks: list[TaskSpec],
    task_plan: list[dict[str, Any]],
    report: ArtifactGateReport,
) -> None:
    plan_by_id = {str(item["task_id"]): item for item in task_plan}
    grouped: dict[str | None, list[TaskSpec]] = {}
    for task in tasks:
        grouped.setdefault(task.game_url_suffix, []).append(task)

    launcher = GameLauncher(
        game_name=game_id,
        base_dir=root / "games" / "benchmark",
    )
    base_url = launcher.start()
    # Stop after the first failing variant; later checks run after its repair.
    checked = 0
    try:
        with tempfile.TemporaryDirectory(prefix="builder_gate_", dir=temporary_dir("builder")) as temporary:
            for suffix, variant_tasks in grouped.items():
                if report.errors:
                    remaining = len(grouped) - checked
                    report.evidence.append(
                        f"stopped after the first failing variant; {remaining} further "
                        f"start variant(s) were not checked this pass"
                    )
                    break
                checked += 1
                manager: BrowserGameManager | None = None
                variant_timeout_s = (
                    3 * game.readiness_timeout_s + 120.0
                    if game.readiness_timeout_s else VARIANT_TIMEOUT_S
                )
                try:
                    try:
                        unity_profile = load_profile_for_game(
                            game_id, root / "catalog" / "unity"
                        )
                    except (OSError, TypeError, ValueError):
                        unity_profile = None
                    manager = BrowserGameManager(
                        BrowserConfig(
                            game_url=append_url_suffix(base_url, suffix),
                            width=game.width,
                            height=game.height,
                            headless=True,
                            speed_multiplier=game.speed_multiplier,
                            readiness_timeout_s=game.readiness_timeout_s,
                            screenshot_dir=Path(temporary),
                            random_seed=42,
                            unity_profile=unity_profile,
                            block_external_network=True,
                        )
                    )
                    await asyncio.wait_for(
                        _check_variant(
                            manager, game_id, suffix, variant_tasks, plan_by_id, report
                        ),
                        timeout=variant_timeout_s,
                    )
                except asyncio.TimeoutError:
                    report.errors.append(
                        f"task variant {suffix or '(default)'} stopped responding; "
                        f"gave up after {variant_timeout_s:.0f}s"
                    )
                finally:
                    if manager is not None:
                        try:
                            await asyncio.wait_for(
                                manager.close(), timeout=CLOSE_TIMEOUT_S
                            )
                        except Exception:  # noqa: BLE001
                            report.evidence.append(
                                f"variant {suffix or '(default)'} did not shut its "
                                f"browser down cleanly"
                            )
    finally:
        launcher.stop()

    if not report.errors:
        report.evidence.append(
            f"{len(grouped)} task start variant(s) passed offline API and reset checks"
        )


def _validate_state(
    game_id: str,
    state: dict[str, Any] | None,
    tasks: list[TaskSpec],
    plan_by_id: dict[str, dict[str, Any]],
    report: ArtifactGateReport,
) -> None:
    label = ", ".join(task.task_id for task in tasks)
    if not isinstance(state, dict):
        report.errors.append(f"{label}: gameAPI.getState() returned no object")
        return
    missing = sorted(REQUIRED_STATE_FIELDS - set(state))
    if missing:
        report.errors.append(f"{label}: game state is missing {', '.join(missing)}")
        return
    if state.get("gameId") != game_id:
        report.errors.append(f"{label}: gameId does not match {game_id}")
    if not isinstance(state.get("schemaVersion"), str):
        report.errors.append(f"{label}: schemaVersion is not a string")
    for field_name in ("timestampMs", "gameTimeMs"):
        value = state.get(field_name)
        if not _finite_number(value):
            report.errors.append(f"{label}: {field_name} is not numeric")
    if not isinstance(state.get("status"), str):
        report.errors.append(f"{label}: status is not a string")
    if state.get("is_actionable") is not True:
        report.errors.append(f"{label}: is_actionable is not true at task start")
    if not isinstance(state.get("game_state"), dict):
        report.errors.append(f"{label}: game_state is not an object")
    if not isinstance(state.get("metrics"), dict):
        report.errors.append(f"{label}: metrics is not an object")

    terminal = state.get("terminal")
    if not isinstance(terminal, dict) or not isinstance(terminal.get("isTerminal"), bool):
        report.errors.append(f"{label}: terminal.isTerminal is not boolean")
    elif terminal.get("outcome") not in {None, "success", "fail"}:
        report.errors.append(f"{label}: terminal.outcome must be success, fail, or null")

    for task in tasks:
        config = task.evaluator_config
        score_paths: list[str] = []
        score_field = config.get("score_field")
        if isinstance(score_field, str):
            score_paths.append(score_field)
        aggregate = config.get("aggregate_score_fields")
        if isinstance(aggregate, list):
            score_paths.extend(value for value in aggregate if isinstance(value, str))
        for path in dict.fromkeys(score_paths):
            value = _path_value(state, path)
            if value is _MISSING:
                report.errors.append(f"{task.task_id}: state path {path!r} is missing")
            elif not _finite_number(value):
                report.errors.append(
                    f"{task.task_id}: evaluator score path {path!r} is not numeric"
                )

        other_paths: list[str] = []
        end_field = config.get("end_field")
        if isinstance(end_field, str) and end_field:
            other_paths.append(end_field)
        metric_fields = config.get("metrics_fields")
        if isinstance(metric_fields, list):
            other_paths.extend(value for value in metric_fields if isinstance(value, str))
        planned_signal = plan_by_id.get(task.task_id, {}).get("progress_signal")
        if isinstance(planned_signal, dict) and isinstance(planned_signal.get("field"), str):
            progress_path = planned_signal["field"]
            progress_value = _path_value(state, progress_path)
            if progress_value is _MISSING:
                report.errors.append(
                    f"{task.task_id}: state path {progress_path!r} is missing"
                )
            elif not _finite_number(progress_value):
                report.errors.append(
                    f"{task.task_id}: progress signal {progress_path!r} is not numeric"
                )
        for path in dict.fromkeys(other_paths):
            if not _has_path(state, path):
                report.errors.append(f"{task.task_id}: state path {path!r} is missing")


async def _check_variant(
    manager: BrowserGameManager,
    game_id: str,
    suffix: str | None,
    variant_tasks: list[TaskSpec],
    plan_by_id: dict[str, Any],
    report: ArtifactGateReport,
) -> None:
    """One variant's runtime checks, as a unit the caller can put a deadline on."""
    await manager.start()
    ready = await manager.wait_until_actionable(
        stage="builder-gate",
        timeout_s=manager.config.readiness_timeout_s or COLD_START_TIMEOUT_S,
    )
    if not ready:
        report.errors.append(
            f"task variant {suffix or '(default)'} never became actionable"
        )
        return
    state = await manager.get_game_state()
    _validate_state(game_id, state, variant_tasks, plan_by_id, report)
    await _validate_reset(manager, game_id, variant_tasks, plan_by_id, report)
    if manager.blocked_external_urls:
        report.evidence.append(
            f"blocked {len(manager.blocked_external_urls)} external request(s) "
            f"for variant {suffix or '(default)'}"
        )


async def _validate_reset(
    manager: BrowserGameManager,
    game_id: str,
    tasks: list[TaskSpec],
    plan_by_id: dict[str, Any],
    report: ArtifactGateReport,
) -> None:
    for attempt in (1, 2):
        if not await manager.reset_game():
            report.errors.append(f"{game_id}: reset attempt {attempt} was not accepted")
            return
        if not await manager.wait_until_actionable(
            stage=f"builder-gate-reset-{attempt}",
            timeout_s=manager.config.readiness_timeout_s or RESET_TIMEOUT_S,
        ):
            report.errors.append(f"{game_id}: reset attempt {attempt} never became actionable")
            return
        state = await manager.get_game_state()
        before = len(report.errors)
        _validate_state(game_id, state, tasks, plan_by_id, report)
        if len(report.errors) > before:
            report.errors[before:] = [
                f"reset attempt {attempt}: {error}" for error in report.errors[before:]
            ]
            return


def _has_path(state: dict[str, Any], path: str) -> bool:
    return _path_value(state, path) is not _MISSING


_MISSING = object()


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _path_value(state: dict[str, Any], path: str) -> Any:
    current: Any = state
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


__all__ = ["ArtifactGateReport", "validate_artifact"]
