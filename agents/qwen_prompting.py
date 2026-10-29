"""One Qwen computer-use prompt contract shared by training and inference."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from harnesses.actions import REPORT_ACTION, ActionContract


QWEN_ACTION_PROMPT_MARKER = "# Computer-use output"
CURRENT_STEP_PROMPT = "# Current step\nCurrent game screen:\n"
NEXT_ACTION_PROMPT = "\nReturn the next action."
# How much a model thinks is set by its thinking level (request fields in its model
# profile), not by this text.
THINKING_RULE = (
    "Think in the thinking channel, then emit only the <tool_call> as the final "
    "answer, with no prose outside the tool-call tags."
)


def build_qwen_action_instruction(
    *,
    width: int,
    height: int,
    contract: ActionContract,
    allowed_keys: Sequence[str] = (),
    allow_clicks: bool = True,
    allow_typing: bool = True,
    thinking: bool = False,
) -> str:
    """Return the sole model-facing Qwen action protocol.

    The action list and example are restricted to controls the current game can
    actually use.  This avoids teaching a click-only game to copy a keyboard
    example, and prevents values such as ``key="wait"``.
    """
    keys = sorted({str(key).strip() for key in allowed_keys if str(key).strip()})
    action_lines: list[str] = []
    if keys:
        action_lines.extend(
            [
                "- press_key: key, optional duration.",
                "- press_keys: keys, optional duration; press simultaneously, then release.",
                "- set_keys: keys, optional duration; keep these keys held after the action. Empty keys releases all held keys.",
                "- key_chunk: ordered steps containing keys and duration, plus release_after.",
            ]
        )
    if allow_typing:
        action_lines.append("- type: text.")
    if allow_clicks:
        action_lines.extend(
            [
                "- click: coordinate, optional button (left/right/middle, default left); omit duration.",
                "- click_hold: coordinate, optional button and duration.",
                "- mouse_move: coordinate.",
                "- drag: start_coordinate, end_coordinate, optional button, duration, and steps.",
                "- scroll: delta_x and delta_y.",
            ]
        )
    action_lines.append("- wait: optional duration; advance time without changing held keys.")
    if contract.report:
        action_lines.append(f"- {REPORT_ACTION}: note, saying exactly what you saw.")
    if contract.note:
        action_lines.append("Every action also takes note: one short sentence on why.")
    if contract.notes:
        action_lines.append("On a review step, the action also takes notes.")

    if keys:
        example_action: dict[str, Any] = {"action": "press_key", "key": keys[0]}
    elif allow_clicks:
        example_action = {"action": "click", "coordinate": [500, 500]}
    else:
        example_action = {"action": "wait", "duration": 0.2}
    if contract.note:
        example_action["note"] = "why this action"
    arguments: dict[str, Any]
    if contract.max_actions == 1:
        arguments = example_action
    else:
        arguments = {"actions": [example_action]}
    example = json.dumps(
        {"name": "computer_use", "arguments": arguments},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    key_rule = (
        "Allowed key values: " + ", ".join(json.dumps(key) for key in keys) + ". "
        "Do not use any other value as a key."
        if keys
        else "This game has no keyboard controls; do not emit a keyboard action."
    )
    click_rule = (
        "Mouse actions are allowed."
        if allow_clicks
        else "Mouse actions are not allowed in this game."
    )
    coordinate_rule = (
        f"Coordinates are [x, y] in a normalized 0-1000 range mapped to the "
        f"{width}x{height} viewport."
        if allow_clicks
        else ""
    )
    response_rule = (
        THINKING_RULE
        if thinking
        else (
            "Encode the action plan as one JSON object inside <tool_call></tool_call>. "
            "Do not write prose outside the tool-call tags or a Markdown fence."
        )
    )
    return f"""{QWEN_ACTION_PROMPT_MARKER}
{contract.instructions()}
{response_rule} Put every action parameter directly inside
`arguments`; never put function-call syntax inside the `action` string.

Example:
<tool_call>{example}</tool_call>

{coordinate_rule}
{key_rule}
{click_rule}

Available actions and fields:
{chr(10).join(action_lines)}"""


def canonical_action_to_qwen_args(
    action: Mapping[str, Any],
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Convert a runtime pixel action back to Qwen's normalized arguments."""
    out = dict(action)
    if out.get("action") == "key_chunk":
        clean_steps = []
        for step in out.get("steps") or []:
            if isinstance(step, Mapping):
                clean_steps.append(
                    {
                        "keys": list(step.get("keys") or []),
                        "duration": step.get("duration"),
                    }
                )
        return {
            "action": "key_chunk",
            "steps": clean_steps,
            "release_after": bool(out.get("release_after", True)),
        }

    viewport_width = max(float(width), 1.0)
    viewport_height = max(float(height), 1.0)

    def normalized(x: Any, y: Any) -> list[float]:
        return [
            round(max(0.0, min(1000.0, float(x) / viewport_width * 1000.0)), 1),
            round(max(0.0, min(1000.0, float(y) / viewport_height * 1000.0)), 1),
        ]

    if out.get("action") == "drag":
        try:
            out["start_coordinate"] = normalized(out.pop("x1"), out.pop("y1"))
            out["end_coordinate"] = normalized(out.pop("x2"), out.pop("y2"))
        except (KeyError, TypeError, ValueError):
            for key in ("x1", "y1", "x2", "y2"):
                out.pop(key, None)
        return out
    if "coordinate" not in out and "x" in out and "y" in out:
        try:
            out["coordinate"] = normalized(out.pop("x"), out.pop("y"))
        except (TypeError, ValueError):
            out.pop("x", None)
            out.pop("y", None)
    return out


def build_qwen_user_content(
    *,
    current_image: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]] = (),
    notes: str | None = None,
    review: str | None = None,
    text_type: str = "text",
) -> list[dict[str, Any]]:
    """Serialize visual history and the current screen with stable separators.

    Image blocks are supplied by the caller because dataset and OpenAI-compatible
    APIs use different image block shapes.  All text and image ordering is shared.
    A turn may carry its episode step number (`step`), a placeholder for a screen
    no longer shown (`screen`) and the model's one-sentence `note`; `notes` and
    `review` come after the history, before the current screen.
    """

    def text(value: str) -> dict[str, Any]:
        return {"type": text_type, "text": value}

    content: list[dict[str, Any]] = []
    if history:
        content.append(text("# Previous steps\n"))
        for index, turn in enumerate(history, start=1):
            content.append(text(f"\n## Step {turn.get('step') or index}\n"))
            image = turn.get("image")
            if isinstance(image, Mapping):
                content.append(text("Previous game screen:\n"))
                content.append(dict(image))
            elif turn.get("screen"):
                content.append(text(f"Previous game screen: {turn['screen']}\n"))
            note = str(turn.get("note") or "").strip()
            if note:
                content.append(text(f"\nNote:\n{note}\n"))
            action = str(turn.get("action") or "").strip()
            if action:
                content.append(text(f"\nPrevious action:\n{action}\n"))
            if turn.get("feedback"):
                content.append(text(f"\nExecution feedback:\n{turn['feedback']}\n"))

    if notes:
        content.append(text(f"\n# Your notes\n{notes}\n"))
    if review:
        content.append(text(f"\n# Review\n{review}\n"))
    if content:
        content.append(text("\n" + CURRENT_STEP_PROMPT))
    else:
        content.append(text(CURRENT_STEP_PROMPT))
    content.append(dict(current_image))
    content.append(text(NEXT_ACTION_PROMPT))
    return content


__all__ = [
    "CURRENT_STEP_PROMPT",
    "NEXT_ACTION_PROMPT",
    "QWEN_ACTION_PROMPT_MARKER",
    "build_qwen_action_instruction",
    "build_qwen_user_content",
    "canonical_action_to_qwen_args",
]
