"""Prompts through the evaluation harness's own code, from a dataset's content snapshot.

A dataset stores the game rules, controls and task prompts it was built with; the
text around them (template, action protocol, memory layout) comes from the same
functions the online harness calls, so training and evaluation prompts cannot drift.

A game is described as the catalog describes it now (what evaluation shows); a game no
longer in the catalog as its episodes recorded it. A task is always the instruction its
episodes were given, so an episode is never labelled with a goal it did not pursue.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from agents.qwen_prompting import build_qwen_user_content, canonical_action_to_qwen_args
from catalog import list_games, load_game
from harnesses.actions import MOUSE_ACTIONS
from harnesses.vlm.prompting import ACTION_CONTRACT, build_prompt_for_game

EMPTY_THINK = "<think>\n\n</think>\n\n"


def snapshot_game(game_id: str) -> dict[str, Any]:
    game = load_game(game_id)
    return {
        "game_rules": game.game_rules or "",
        "width": int(game.width),
        "height": int(game.height),
        "role_description": game.player.context.role_description,
        "control_notes": game.player.context.control_notes,
        "allowed_keys": sorted(game.player.controls.allowed_keys),
        "allow_clicks": bool(game.player.controls.allow_clicks),
    }


def recorded_game(summary: dict[str, Any]) -> dict[str, Any]:
    """The game as an episode recorded it (role context only in recent recordings)."""
    catalog = summary.get("catalog") or {}
    role = (catalog.get("roles") or [{}])[0]
    context = role.get("context") or {}
    controls = role.get("controls") or {}
    width, height = summary["screen_size"]
    return {
        "game_rules": catalog.get("game_rules") or "",
        "width": int(width),
        "height": int(height),
        "role_description": context.get("role_description") or "",
        "control_notes": context.get("control_notes") or "",
        "allowed_keys": sorted(controls.get("allowed_keys") or []),
        "allow_clicks": bool(controls.get("allow_clicks", True)),
    }


def snapshots(summaries: dict[str, dict[str, Any]]) -> tuple[dict[str, dict], dict[str, str]]:
    """Game snapshots and task prompts (keyed "game/task") for episodes given by id ->
    summary. Episodes of one task must agree on its instruction."""
    known = set(list_games())
    games: dict[str, dict] = {}
    tasks: dict[str, str] = {}
    for episode_id, summary in sorted(summaries.items()):
        game_id = summary["game_id"]
        if game_id not in games:
            games[game_id] = snapshot_game(game_id) if game_id in known else recorded_game(summary)
        key = f"{game_id}/{(summary.get('task') or {}).get('id')}"
        instruction = (summary.get("task") or {}).get("instruction") or ""
        if tasks.setdefault(key, instruction) != instruction:
            raise ValueError(f"episodes of {key} were given different instructions (e.g. {episode_id})")
    return games, tasks


def allowed_by(snapshot: dict[str, Any]):
    """Whether a label is something the game's prompt allows (the prompt lists the
    allowed keys and whether mouse actions exist)."""
    keys = set(snapshot["allowed_keys"])

    def check(label: dict[str, Any]) -> bool:
        kind = label.get("action")
        if kind in MOUSE_ACTIONS:
            return snapshot["allow_clicks"]
        if kind == "press_key":
            used = [label.get("key")]
        elif kind in {"press_keys", "set_keys"}:
            used = list(label.get("keys") or [])
        elif kind == "key_chunk":
            used = [k for step in label.get("steps") or [] for k in step.get("keys") or []]
        else:
            return True
        return all(k in keys for k in used)

    return check


def system_prompt(snapshot: dict[str, Any], task_prompt: str, *, thinking: bool,
                  codec: str = "qwen") -> str:
    game = SimpleNamespace(
        game_rules=snapshot["game_rules"], width=snapshot["width"], height=snapshot["height"],
        player=SimpleNamespace(
            context=SimpleNamespace(role_description=snapshot["role_description"],
                                    control_notes=snapshot["control_notes"]),
            controls=SimpleNamespace(allowed_keys=tuple(snapshot["allowed_keys"]),
                                     allow_clicks=snapshot["allow_clicks"])),
    )
    return build_prompt_for_game(
        game=game, task_prompt=task_prompt, codec=codec, contract=ACTION_CONTRACT,
        memory_enabled=True, thinking=thinking,
    )


def memory_action(label: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """A previous action as the online harness shows it in memory."""
    args = canonical_action_to_qwen_args(label, width=snapshot["width"], height=snapshot["height"])
    return json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)


def tool_call(label: dict[str, Any], snapshot: dict[str, Any], *, note: str = "") -> str:
    args = canonical_action_to_qwen_args(label, width=snapshot["width"], height=snapshot["height"])
    args["note"] = note
    return "<tool_call>\n" + json.dumps({"name": "computer_use", "arguments": args}, ensure_ascii=False) + "\n</tool_call>"


def user_content(current_image: str, history: list) -> list[dict[str, Any]]:
    """Chronological actions with optional images and short notes, as in the live harness."""
    return build_qwen_user_content(
        current_image={"type": "image", "image": current_image},
        history=history,
    )
