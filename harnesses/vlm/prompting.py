"""Shared prompt construction for VLM play and training samples."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from catalog.prompt_templates import DEFAULT_PROMPT_TEMPLATE_ID, load_prompt_template
from agents.qwen_prompting import build_qwen_action_instruction
from harnesses.actions import ActionContract
from harnesses.base import HarnessContext


CATALOG_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "catalog" / "prompt_templates"
# One action per model turn, duration optional: the contract evaluation runs and
# training prompts are both built with.
ACTION_CONTRACT = ActionContract(max_actions=1, duration_policy="optional", note=True)


@cache
def _environment(templates_dir: str | Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(templates_dir),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _join(*sections: str | None) -> str:
    return "\n\n".join(
        str(section).strip()
        for section in sections
        if section and str(section).strip()
    )


def canonical_call_rule(
    *,
    contract: ActionContract,
    allowed_keys: tuple[str, ...],
    allow_clicks: bool,
    width: int,
    height: int,
) -> str:
    """Say that a function call always names its action, with an example the game can use:
    without a forced tool choice, models sometimes send bare coordinates."""
    keys = sorted({str(key).strip() for key in allowed_keys if str(key).strip()})
    examples: list[dict[str, Any]] = []
    if allow_clicks:
        examples.append({"action": "click", "x": width // 2, "y": height // 2})
    if keys:
        examples.append({"action": "press_key", "key": keys[0]})
    if not examples:
        examples.append({"action": "wait"})
    if contract.note:
        for example in examples:
            example["note"] = "why this action"
    return (
        "Every computer_use call must include `action`; coordinates or keys alone are not "
        "an action. Example arguments: " + " or ".join(json.dumps(example) for example in examples)
    )


def build_prompt_for_game(
    *,
    game: Any,
    task_prompt: str,
    codec: str,
    contract: ActionContract,
    memory_enabled: bool,
    width: int | None = None,
    height: int | None = None,
    thinking: bool = False,
) -> str:
    """Render the system prompt from explicit parts for eval and SFT export."""
    template = load_prompt_template(DEFAULT_PROMPT_TEMPLATE_ID)
    player = game.player
    memory_note = None
    if memory_enabled or codec == "qwen":
        memory_note = (
            "The user message may contain explicitly labelled previous steps. Treat them "
            "as chronological short-term context; act on the labelled current step."
        )
    output_format = None
    if codec == "qwen":
        output_format = build_qwen_action_instruction(
            width=int(width or game.width),
            height=int(height or game.height),
            contract=contract,
            allowed_keys=tuple(player.controls.allowed_keys),
            allow_clicks=bool(player.controls.allow_clicks),
            thinking=thinking,
        )
    return (
        _environment(CATALOG_PROMPTS_DIR)
        .get_template(template.template_name)
        .render(
            game_rules_block=game.game_rules or "",
            task_instruction_block=task_prompt,
            role_control_block_computer_use=_join(
                player.context.role_description,
                player.context.control_notes,
                memory_note,
            ),
            model_output_format_block=(
                output_format if codec == "qwen" else _join(
                    contract.instructions(),
                    canonical_call_rule(
                        contract=contract,
                        allowed_keys=tuple(player.controls.allowed_keys),
                        allow_clicks=bool(player.controls.allow_clicks),
                        width=int(width or game.width),
                        height=int(height or game.height),
                    ),
                )
            ),
        )
        .strip()
    )


def build_prompt(
    *,
    context: HarnessContext,
    profile: Any,
    contract: ActionContract,
    memory_enabled: bool,
    thinking: bool,
) -> str:
    task_prompt = (
        context.task.task_prompt
        if context.task is not None
        else "Play the game naturally and make as much progress as possible."
    )
    return build_prompt_for_game(
        game=context.game,
        task_prompt=task_prompt,
        codec=profile.codec,
        contract=contract,
        memory_enabled=memory_enabled,
        thinking=thinking,
    )


__all__ = ["ACTION_CONTRACT", "build_prompt", "build_prompt_for_game"]
