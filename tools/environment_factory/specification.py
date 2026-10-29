"""Published game and task definitions used when reconstructing a benchmark."""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import yaml

from catalog.games._base import GameDefinition
from catalog.tasks._base import TaskSpec


def load_specification(path: Path, game_id: str) -> dict[str, Any]:
    specification = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(specification, dict):
        raise ValueError(f"Task specification must be a mapping: {path}")
    game = GameDefinition.from_mapping(specification.get("game"))
    if game.game_name != game_id:
        raise ValueError(f"Task specification names {game.game_name}, expected {game_id}")
    game.player
    tasks = specification.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"Task specification has no tasks: {path}")
    ids = set()
    for raw in tasks:
        task = TaskSpec.from_mapping(raw)
        if task.game_id != game_id or task.task_id in ids:
            raise ValueError(f"Wrong game or duplicate task in {path}: {task.task_id}")
        ids.add(task.task_id)
    return specification


def prepare_specification(root: Path, specification: dict[str, Any]) -> None:
    """Seed a new catalog with the published definitions without replacing work."""
    game_id = specification["game"]["game_name"]
    game_path = root / "catalog" / "games" / f"{game_id}.yaml"
    task_dir = root / "catalog" / "tasks" / game_id
    if not game_path.exists():
        game_path.parent.mkdir(parents=True, exist_ok=True)
        game_path.write_text(yaml.safe_dump(specification["game"], sort_keys=False,
                                           allow_unicode=True), encoding="utf-8")
    if not any(task_dir.glob("*.yaml")):
        task_dir.mkdir(parents=True, exist_ok=True)
        for task in specification["tasks"]:
            (task_dir / f"{task['task_id']}.yaml").write_text(
                yaml.safe_dump(task, sort_keys=False, allow_unicode=True), encoding="utf-8")


def specification_errors(root: Path, specification: dict[str, Any]) -> list[str]:
    """Compare the delivered catalog with the published gameplay contract."""
    expected_game = GameDefinition.from_mapping(specification["game"])
    game_id = expected_game.game_name
    errors = []
    try:
        actual_game = GameDefinition.from_mapping(yaml.safe_load(
            (root / "catalog" / "games" / f"{game_id}.yaml").read_text(encoding="utf-8")))
        # The source location and loading timeout are not gameplay settings.
        actual_game = replace(actual_game, url=None, readiness_timeout_s=None)
        expected_game = replace(expected_game, url=None, readiness_timeout_s=None)
        changed = [field.name for field in fields(expected_game)
                   if getattr(actual_game, field.name) != getattr(expected_game, field.name)]
        if changed:
            errors.append("published game settings changed: " + ", ".join(changed))
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"cannot read published game settings: {exc}")

    expected = {task["task_id"]: task for task in specification["tasks"]}
    paths = {path.stem: path for path in (root / "catalog" / "tasks" / game_id).glob("*.yaml")}
    if paths.keys() != expected.keys():
        errors.append("task files must match the complete published task set")
    for task_id in sorted(paths.keys() & expected.keys()):
        try:
            actual_task = TaskSpec.from_mapping(yaml.safe_load(paths[task_id].read_text(encoding="utf-8")))
            expected_task = TaskSpec.from_mapping(expected[task_id])
            changed = [field.name for field in fields(expected_task)
                       if getattr(actual_task, field.name) != getattr(expected_task, field.name)]
            if changed:
                errors.append(f"{task_id} changed published fields: " + ", ".join(changed))
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"cannot read published task {task_id}: {exc}")
    return errors
