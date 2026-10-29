"""Translate provider outputs into the one canonical keyboard/mouse action space."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from harnesses.actions import (
    ACTION_OPTIONS,
    Action,
    ActionContract,
    CANONICAL_ACTIONS,
    MOUSE_BUTTONS,
    REPORT_ACTION,
    flatten_action_payload,
    normalize_key,
    split_keys,
)


def _value(payload: Any, *names: str) -> Any:
    for name in names:
        if isinstance(payload, Mapping) and name in payload:
            return payload.get(name)
        value = getattr(payload, name, None)
        if value is not None:
            return value
    return None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _keys(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = split_keys(value)
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        return []
    return [key for item in raw if (key := normalize_key(item))]


def _with_options(action: Action, args: Mapping[str, Any]) -> Action:
    """Copy the optional parameters the action takes (harnesses.actions.ACTION_OPTIONS)
    from the model's arguments; an unknown mouse button is left out (the default)."""
    for name in ACTION_OPTIONS[action["action"]]:
        value = args.get(name)
        if value is None or name in action:
            continue
        if name == "button":
            value = str(value).lower()
            if value not in MOUSE_BUTTONS:
                continue
        action[name] = value
    return action


def canonical_action_schema(
    *,
    strict: bool = False,
    contract: ActionContract | None = None,
) -> dict[str, Any]:
    """JSON schema shared by ordinary vision models that support function tools."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": sorted(CANONICAL_ACTIONS)},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "x1": {"type": "number"},
            "y1": {"type": "number"},
            "x2": {"type": "number"},
            "y2": {"type": "number"},
            "delta_x": {"type": "number"},
            "delta_y": {"type": "number"},
            "key": {"type": "string"},
            "keys": {"type": "array", "items": {"type": "string"}},
            "text": {"type": "string"},
            "button": {"type": "string", "enum": sorted(MOUSE_BUTTONS)},
            "duration": {"type": "number", "minimum": 0},
            "press_enter": {"type": "boolean"},
            "steps": {"anyOf": [
                {"type": "integer", "minimum": 1,
                 "description": "drag: number of pointer movement steps."},
                {
                    "type": "array",
                    "description": "key_chunk: ordered key states, each held for its duration.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "keys": {"type": "array", "items": {"type": "string"}},
                            "duration": {"type": "number", "minimum": 0},
                        },
                        "required": ["keys", "duration"],
                    },
                },
            ]},
            "release_after": {"type": "boolean"},
            "note": {"type": "string"},
        },
        "required": ["action"],
    }
    if strict:
        schema["additionalProperties"] = False
    return (contract or ActionContract()).schema(schema)


def canonical_tool(
    *,
    chat_format: bool,
    width: int,
    height: int,
    contract: ActionContract | None = None,
) -> dict[str, Any]:
    resolved_contract = contract or ActionContract()
    function = {
        "name": "computer_use",
        "description": (
            f"{resolved_contract.instructions()} Coordinates are absolute CSS pixels in a "
            f"{width}x{height} browser viewport. For drag use x1,y1,x2,y2. Every call must "
            "include `action`."
        ),
        "parameters": canonical_action_schema(
            strict=False,
            contract=resolved_contract,
        ),
    }
    return {"type": "function", "function": function} if chat_format else {
        "type": "function",
        **function,
        "strict": False,
    }


def parse_json_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def chat_message_thinking(message: Any) -> str | None:
    """Extract actual provider thinking, never a generated explanation field."""
    for field in ("reasoning_content", "reasoning_details", "reasoning"):
        value = _value(message, field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts = [
                str(_value(part, "text", "content") or "")
                for part in value
                if _value(part, "type") in (None, "reasoning.text", "reasoning", "text")
            ]
            thought = "\n".join(part for part in parts if part.strip()).strip()
            if thought:
                return thought
    content = str(_value(message, "content") or "")
    match = re.search(r"<think>([\s\S]*?)</think>", content, re.IGNORECASE)
    return match.group(1).strip() if match and match.group(1).strip() else None


def chat_tool_arguments(response: Any) -> dict[str, Any] | None:
    """The arguments of the response's computer_use call as the model wrote them,
    including its action `note` and review `notes`; None without such a call."""
    choices = _value(response, "choices") or []
    message = _value(choices[0], "message") if choices else None
    if message is None:
        return None
    for call in _value(message, "tool_calls") or []:
        function = _value(call, "function") or call
        if str(_value(function, "name") or "") == "computer_use":
            return parse_json_arguments(_value(function, "arguments"))
    # Without a forced tool_choice a model may write the call into its text instead.
    for match in _TOOL_CALL_RE.finditer(str(_value(message, "content") or "")):
        try:
            call = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(call, Mapping) and str(call.get("name") or "computer_use") == "computer_use":
            return parse_json_arguments(call.get("arguments"))
    # The Qwen action decoder also accepts a whole JSON final answer.
    final = str(_value(message, "content") or "").strip()
    if final.startswith("```json") and final.endswith("```"):
        final = final[len("```json"):-3].strip()
    payload = parse_json_arguments(final)
    if payload.get("name", "computer_use") == "computer_use":
        if isinstance(payload.get("arguments"), Mapping):
            return dict(payload["arguments"])
        if isinstance(payload.get("action"), str):
            return payload
        if isinstance(payload.get("action"), Mapping):
            return dict(payload["action"])
    return None


def parse_chat_tool_action(response: Any) -> tuple[list[Action], str | None]:
    choices = _value(response, "choices") or []
    message = _value(choices[0], "message") if choices else None
    reasoning = chat_message_thinking(message) if message is not None else None
    payload = chat_tool_arguments(response)
    if payload is None:
        return [], str(reasoning or "") or None
    # Review notes are for the harness, not part of the action.
    payload.pop("notes", None)
    # A short action note is never used as a substitute for native thinking.
    return flatten_action_payload(payload), str(reasoning or "") or None


def _point(value: Any, width: int, height: int, *, normalized: bool) -> tuple[float, float] | None:
    coords: tuple[float, float] | None = None
    if isinstance(value, Mapping):
        x, y = _float(value.get("x")), _float(value.get("y"))
        if x is not None and y is not None:
            coords = x, y
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        x, y = _float(value[0]), _float(value[1])
        if x is not None and y is not None:
            coords = x, y
    elif isinstance(value, str):
        numbers = re.findall(r"-?\d+(?:\.\d+)?", value)
        if len(numbers) >= 2:
            coords = float(numbers[0]), float(numbers[1])
    if coords is None:
        return None
    if not normalized:
        return coords
    return (
        max(0.0, min(1000.0, coords[0])) / 1000.0 * width,
        max(0.0, min(1000.0, coords[1])) / 1000.0 * height,
    )


def _canonicalize_args(
    name: str,
    args: Mapping[str, Any],
    *,
    width: int,
    height: int,
    normalized: bool,
) -> Action | None:
    action_type = str(args.get("action") or (name if name != "computer_use" else "")).lower()
    if action_type == REPORT_ACTION:
        return {"action": REPORT_ACTION, "note": str(args.get("note") or "").strip()}
    if action_type not in CANONICAL_ACTIONS:
        return None

    if action_type in {"click", "click_hold", "mouse_move"}:
        point = _point(
            args.get("coordinate") or args.get("point") or args.get("start_box")
            or ([args.get("x"), args.get("y")] if "x" in args and "y" in args else None),
            width,
            height,
            normalized=normalized,
        )
        if point is None:
            return None
        action: Action = {"action": action_type, "x": point[0], "y": point[1]}
        return _with_options(action, args)

    if action_type == "drag":
        start = _point(
            args.get("start_coordinate") or args.get("start_point") or args.get("start")
            or ([args.get("x1"), args.get("y1")] if "x1" in args else None),
            width,
            height,
            normalized=normalized,
        )
        end = _point(
            args.get("end_coordinate") or args.get("end_point") or args.get("end")
            or args.get("coordinate")
            or ([args.get("x2"), args.get("y2")] if "x2" in args else None),
            width,
            height,
            normalized=normalized,
        )
        if start is None or end is None:
            return None
        return _with_options(
            {"action": "drag", "x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]}, args
        )

    if action_type in {"press_key", "press_keys", "set_keys"}:
        keys = _keys(args.get("keys", args.get("key")))
        if not keys and action_type != "set_keys":
            return None
        action: Action = (
            {"action": "press_key", "key": keys[0]}
            if len(keys) == 1
            else {"action": "press_keys", "keys": keys}
        )
        if action_type == "set_keys":
            action = {"action": "set_keys", "keys": keys}
        return _with_options(action, args)

    if action_type == "type":
        text = args.get("text", args.get("content"))
        if text in (None, ""):
            return None
        action = {"action": "type", "text": str(text)}
        x, y = _float(args.get("x")), _float(args.get("y"))
        if x is not None and y is not None:
            action.update(x=x, y=y)
        if args.get("press_enter") is not None:
            action["press_enter"] = bool(args["press_enter"])
        return _with_options(action, args)
    if action_type == "scroll":
        return {
            "action": "scroll",
            "delta_x": _float(args.get("delta_x")) or 0.0,
            "delta_y": _float(args.get("delta_y")) or 0.0,
        }
    if action_type == "key_chunk":
        raw_steps = args.get("steps") or args.get("chunk") or []
        if not isinstance(raw_steps, (list, tuple)):
            return None
        steps: list[dict[str, Any]] = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, Mapping):
                return None
            duration = _float(raw_step.get("duration"))
            if duration is None or duration < 0:
                return None
            steps.append(
                {
                    "keys": _keys(raw_step.get("keys", raw_step.get("key"))),
                    "duration": duration,
                }
            )
        if not steps:
            return None
        return {
            "action": "key_chunk",
            "steps": steps,
            "release_after": bool(args.get("release_after", True)),
        }
    return _with_options({"action": "wait"}, args)


_TOOL_CALL_RE = re.compile(
    r"<tool(?:\s+|_)call>\s*(\{[\s\S]*?\})\s*</tool(?:\s+|_)call>",
    re.IGNORECASE,
)


def parse_qwen_action(text: str, width: int, height: int) -> tuple[list[Action], str | None]:
    for match in _TOOL_CALL_RE.finditer(text or ""):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        args = parse_json_arguments(payload.get("arguments"))
        raw_actions = flatten_action_payload(args)
        actions = [
            action
            for item in raw_actions
            if (
                action := _canonicalize_args(
                    str(item.get("action") or payload.get("name") or "computer_use"),
                    item,
                    width=width,
                    height=height,
                    normalized=True,
                )
            )
        ]
        if actions:
            thought = re.search(r"<think>([\s\S]*?)</think>", text or "", re.IGNORECASE)
            return actions, thought.group(1).strip() if thought else None
    # Some OpenAI-compatible Qwen servers return the final action as plain JSON
    # even when the prompt requests a <tool_call> wrapper. Only parse a whole
    # JSON final answer here, never a JSON-looking fragment inside thinking.
    final = (text or "").strip()
    if final.startswith("```json") and final.endswith("```"):
        final = final[len("```json"):-3].strip()
    if final.startswith("{"):
        try:
            payload = json.loads(final)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, Mapping):
            args = payload.get("arguments") or payload.get("action")
            if isinstance(args, str):
                args = payload
            if isinstance(args, Mapping):
                action = _canonicalize_args(
                    str(args.get("action") or "computer_use"), args,
                    width=width, height=height, normalized=True,
                )
                if action:
                    return [action], None
    return [], None


__all__ = [
    "CANONICAL_ACTIONS",
    "canonical_action_schema",
    "canonical_tool",
    "chat_message_thinking",
    "chat_tool_arguments",
    "parse_chat_tool_action",
    "parse_qwen_action",
]
