"""Per-game bot policies. Each game has exactly one policy."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from typing import Any

from harnesses.policy.workspace import policy_workspace

from .base import Policy


def builtin_policy_path(game_id: str) -> Path:
    stem = str(game_id).strip().replace("-", "_")
    workspace = policy_workspace()
    if workspace is not None:
        return workspace / "policies" / f"g{stem}.py"
    return Path(__file__).with_name(f"g{stem}.py")


def builtin_policy_tasks_path(game_id: str) -> Path:
    """The list of tasks the game's policy completes, kept beside the policy."""
    return builtin_policy_path(game_id).with_suffix(".tasks.json")


def policy_completed_tasks(game_id: str) -> list[str] | None:
    """The tasks the game's policy completes, or None for a policy without a list,
    whose tasks collection then tries until it gives up."""
    path = builtin_policy_tasks_path(game_id)
    if not path.is_file():
        return None
    return list(json.loads(path.read_text(encoding="utf-8")))


def _load_builtin_policy(game_id: str) -> Policy:
    path = builtin_policy_path(game_id)
    if not path.is_file():
        raise ValueError(f"No auto-collect policy for {game_id}: {path}")
    if policy_workspace() is None:
        module = importlib.import_module(f"{__name__}.{path.stem}")
    else:
        spec = importlib.util.spec_from_file_location(
            f"experiment_policy_{path.stem}", path
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load policy module: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    candidates = [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and callable(getattr(value, "act", None))
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Built-in policy module must define exactly one policy class: {path}"
        )
    return _instantiate_policy(candidates[0], source=game_id)


def _instantiate_policy(candidate: Any, *, source: str) -> Policy:
    is_factory = isinstance(candidate, type) or (
        callable(candidate) and not hasattr(candidate, "act")
    )
    policy = candidate() if is_factory else candidate
    if not callable(getattr(policy, "act", None)):
        raise ValueError(
            f"Policy {source!r} must provide an act(policy_state) method."
        )
    return policy


def load_policy(policy_spec: str) -> Policy:
    """Load `module:object` or `/path/to/file.py:object` policy specs."""
    module_spec, separator, symbol = str(policy_spec).strip().partition(":")
    if not separator or not module_spec or not symbol:
        raise ValueError("--policy must use module:object or /path/to/file.py:object.")

    path = Path(module_spec).expanduser()
    if path.suffix == ".py" or path.exists():
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"Policy module does not exist: {path}")
        spec = importlib.util.spec_from_file_location(f"user_policy_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load policy module: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_spec)

    if not hasattr(module, symbol):
        raise ValueError(f"Policy object {symbol!r} was not found in {module_spec!r}.")
    return _instantiate_policy(getattr(module, symbol), source=policy_spec)


def get_policy(game_id: str, policy_spec: str | None = None) -> Policy:
    if policy_spec:
        return load_policy(policy_spec)
    return _load_builtin_policy(game_id)


def is_terminal(state: dict[str, Any] | None) -> bool:
    if not isinstance(state, dict):
        return False
    terminal = state.get("terminal")
    return isinstance(terminal, dict) and bool(terminal.get("isTerminal"))
