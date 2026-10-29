"""The canonical action vocabulary and the one configurable agent output contract.

Everything that describes an action -- its optional parameters, what an omitted
duration means, key names -- is defined here once; prompts, decoders, the executor
and the training data builder all read it from this module.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal


Action = dict[str, Any]
DurationPolicy = Literal["default", "optional", "required"]
MAX_ACTION_DURATION_S = 10.0

# What a press or click with no duration lasts. An action that names no duration is a
# tap -- the game reads that the input happened, not how long it was held -- so it only
# has to outlast a frame for the game to see it at all.
TAP_DURATION_S = 0.05
# A wait, mouse hold or drag with no duration lasts the game's `hold_duration`; this is
# the value when the catalog sets none.
DEFAULT_ACTION_DURATION = 0.2

# Optional parameters of each action besides what it acts on (keys, text,
# coordinates). Decoders pass them through unchanged, so a parameter a prompt offers
# cannot be dropped on the way to the executor.
ACTION_OPTIONS: dict[str, tuple[str, ...]] = {
    "click": ("button", "duration"),
    "click_hold": ("button", "duration"),
    "mouse_move": ("duration",),
    "drag": ("button", "duration", "steps"),
    "scroll": (),
    "type": ("press_enter",),
    "press_key": ("duration",),
    "press_keys": ("duration",),
    "set_keys": ("duration",),
    "key_chunk": ("release_after",),
    "wait": ("duration",),
}
CANONICAL_ACTIONS = frozenset(ACTION_OPTIONS)
KEY_ACTIONS = frozenset({"press_key", "press_keys", "set_keys", "key_chunk"})
MOUSE_ACTIONS = frozenset({"click", "click_hold", "mouse_move", "drag", "scroll"})
MOUSE_BUTTONS = frozenset({"left", "right", "middle"})

KEY_SPLIT_PATTERN = re.compile(r"[,+\s]+")
KEY_ALIASES = {
    "arrowleft": "ArrowLeft",
    "left": "ArrowLeft",
    "arrowright": "ArrowRight",
    "right": "ArrowRight",
    "arrowup": "ArrowUp",
    "up": "ArrowUp",
    "arrowdown": "ArrowDown",
    "down": "ArrowDown",
    "space": "Space",
    "spacebar": "Space",
    "enter": "Enter",
    "return": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "shift": "Shift",
    "shiftleft": "Shift",
    "shiftright": "ShiftRight",
    "control": "Control",
    "ctrl": "Control",
    "controlleft": "Control",
    "controlright": "ControlRight",
    "alt": "Alt",
    "altleft": "Alt",
    "altright": "AltRight",
    "slash": "/",
    "period": ".",
    "comma": ",",
    "quote": "'",
    "apostrophe": "'",
    "semicolon": ";",
    "backslash": "\\",
    "bracketleft": "[",
    "bracketright": "]",
    "minus": "-",
    "equal": "=",
}
# Every name Playwright's US keyboard layout accepts; keyboard.down/up raise
# "Unknown key" on anything else.
PLAYWRIGHT_KEYS = frozenset(
    [chr(code) for code in range(0x20, 0x7F)]
    + ["\n", "\r"]
    + [f"F{index}" for index in range(1, 13)]
    + [f"Digit{index}" for index in range(10)]
    + [f"Numpad{index}" for index in range(10)]
    + [f"Key{chr(code)}" for code in range(ord("A"), ord("Z") + 1)]
    + """Alt AltGraph AltLeft AltRight ArrowDown ArrowLeft ArrowRight ArrowUp Backquote
    Backslash Backspace BracketLeft BracketRight CapsLock Clear Comma ContextMenu Control
    ControlLeft ControlOrMeta ControlRight Delete End Enter Equal Escape Home Insert Meta
    MetaLeft MetaRight Minus NumLock NumpadAdd NumpadDecimal NumpadDivide NumpadEnter
    NumpadMultiply NumpadSubtract PageDown PageUp Pause Period PrintScreen Quote ScrollLock
    Semicolon Shift ShiftLeft ShiftRight Slash Space Tab""".split()
)


def normalize_key(key: Any) -> str | None:
    """The canonical spelling of a key name: one character lower-cased, known aliases
    resolved, anything else kept as written (the executor decides if it can press it)."""
    if not isinstance(key, str) or not key.strip():
        return None
    text = key.strip()
    if len(text) == 1:
        return text.lower()
    return KEY_ALIASES.get(text.lower(), text)


def split_keys(raw_keys: str) -> list[str]:
    return [part for part in KEY_SPLIT_PATTERN.split(raw_keys.strip()) if part]


# Not an input: a player's report of a suspected environment problem. It ends the
# episode; the Builder checks the observation before it becomes an environment verdict.
REPORT_ACTION = "report_broken"
REPORT_RULE = (
    f"Use {REPORT_ACTION} to report a suspected environment problem. Describe only "
    "what you observed, not a conclusion that the game is broken or the task impossible: "
    "a control the game guide describes that, used as the guide "
    "says, still does not do what the guide says after several tries (name the control, "
    "what you did, what you expected and what happened); a game drawn into only part of "
    "the screen, or things the task needs too small to read; or an error message, a "
    "screen that ignores every input, or a blank page. Losing, being stuck or not knowing "
    "what to do is not a reason to report. A blocked move, locked door, missed click "
    "or unmet prerequisite can be normal gameplay. When unsure, keep playing within "
    "the episode budget. A report ends the episode; it does not decide whether the "
    "environment is valid."
)

NOTE_RULE = (
    "In `note`, write one short sentence on why you chose this action; your later "
    "steps show it."
)
NOTES_MAX_WORDS = 150
NOTES_RULE = (
    "On a step marked as a review, also rewrite your notes in `notes` (at most "
    f"{NOTES_MAX_WORDS} words): progress toward the goal, what works, what failed, and the "
    "plan. Keep a point unless new evidence contradicts it. Leave `notes` out on other "
    "steps; your latest notes are shown before the current screen."
)

OMITTED_DURATION = (
    f"press_key, press_keys and click are {TAP_DURATION_S:g}-second taps, and wait, "
    "click_hold and drag last the game's default time."
)


class ActionContractError(ValueError):
    """Raised when a harness response violates its configured action contract."""


@dataclass(slots=True)
class ActionPlan:
    """One or more canonical actions executed in order."""

    actions: list[Action] = field(default_factory=list)

    @classmethod
    def empty(cls) -> ActionPlan:
        return cls()


@dataclass(frozen=True, slots=True)
class ActionContract:
    """Prompt, schema, and validation constraints for one harness."""

    max_actions: int = 1
    duration_policy: DurationPolicy = "default"
    # Whether the agent may end the episode with REPORT_ACTION.
    report: bool = False
    # Whether `note` is a required one-sentence note kept in history (NOTE_RULE).
    note: bool = False
    # Whether the call carries review `notes` (see NOTES_RULE).
    notes: bool = False

    def __post_init__(self) -> None:
        if self.max_actions <= 0:
            raise ValueError("max_actions must be greater than zero")
        if self.duration_policy not in {"default", "optional", "required"}:
            raise ValueError(f"unsupported duration policy: {self.duration_policy}")
        if self.notes and self.max_actions != 1:
            raise ValueError("review notes need a one-action call")

    def normalize(self, raw: object) -> ActionPlan:
        """Normalize one action, an action list, or an ActionPlan."""
        if raw is None:
            return ActionPlan.empty()
        if isinstance(raw, ActionPlan):
            actions = raw.actions
        elif isinstance(raw, Mapping) and "actions" in raw:
            nested = raw.get("actions")
            if not isinstance(nested, (list, tuple)):
                raise ActionContractError("actions must be a list")
            actions = list(nested)
        elif isinstance(raw, Mapping):
            actions = [raw]
        elif isinstance(raw, (list, tuple)):
            actions = list(raw)
        else:
            raise ActionContractError("action output must be an object or list")

        if len(actions) > self.max_actions:
            raise ActionContractError(
                f"expected at most {self.max_actions} actions, received {len(actions)}"
            )

        normalized: list[Action] = []
        for index, item in enumerate(actions):
            if not isinstance(item, Mapping):
                raise ActionContractError(f"action {index} must be an object")
            action = dict(item)
            # Why the model took the action: a note, not part of what is executed, except
            # that a report's note is the report.
            note = action.pop("note", None)
            action_type = str(action.get("action") or "").strip().lower()
            if self.report and action_type == REPORT_ACTION:
                if len(actions) != 1:
                    raise ActionContractError(f"{REPORT_ACTION} must be the only action")
                normalized.append({"action": REPORT_ACTION, "reason": str(note or "").strip()})
                continue
            if action_type not in CANONICAL_ACTIONS:
                raise ActionContractError(
                    f"action {index} has unsupported type: {action_type or '(missing)'}"
                )
            action["action"] = action_type
            if self.duration_policy == "required" and "duration" not in action:
                raise ActionContractError(f"action {index} requires duration")
            if "duration" in action:
                duration = action["duration"]
                if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                    raise ActionContractError(f"action {index} duration must be a number")
                if float(duration) < 0:
                    raise ActionContractError(f"action {index} duration must be non-negative")
                action["duration"] = min(float(duration), MAX_ACTION_DURATION_S)
                if self.duration_policy == "default":
                    action.pop("duration")
            normalized.append(action)
        return ActionPlan(normalized)

    def instructions(self) -> str:
        """Return the model-facing part of this contract."""
        cardinality = (
            "Return exactly one action."
            if self.max_actions == 1
            else f"Return a list containing at most {self.max_actions} actions in execution order."
        )
        duration = {
            "default": f"Do not provide duration: {OMITTED_DURATION}",
            "optional": (
                f"Duration is optional, in seconds, at most {MAX_ACTION_DURATION_S:g}. "
                f"Without a duration, {OMITTED_DURATION}"
            ),
            "required": (
                "Every action must include a non-negative duration in seconds, no greater "
                f"than {MAX_ACTION_DURATION_S:g}."
            ),
        }[self.duration_policy]
        report = f"\n{REPORT_RULE}" if self.report else ""
        note = f"\n{NOTE_RULE}" if self.note else ""
        notes = f"\n{NOTES_RULE}" if self.notes else ""
        keyboard = (
            "\npress_keys presses its keys simultaneously, then releases them; repeated key "
            "names do not create repeated taps. set_keys keeps its keys held until another "
            "keyboard action changes them; empty keys releases them. wait advances time "
            "without releasing held keys."
        )
        return f"{cardinality}\n{duration}{keyboard}{report}{note}{notes}"

    def schema(self, action_schema: Mapping[str, Any]) -> dict[str, Any]:
        """Wrap one canonical action schema with this contract's cardinality."""
        item_schema = _copy_schema(action_schema)
        properties = item_schema.get("properties")
        if isinstance(properties, dict) and isinstance(properties.get("duration"), dict):
            properties["duration"]["maximum"] = MAX_ACTION_DURATION_S
        if self.duration_policy == "default" and isinstance(properties, dict):
            properties.pop("duration", None)
        if self.report and isinstance(properties, dict) and isinstance(properties.get("action"), dict):
            properties["action"]["enum"] = [*properties["action"].get("enum", []), REPORT_ACTION]
        if self.notes and isinstance(properties, dict):
            properties["notes"] = {"type": "string", "description": "Review steps only: your rewritten notes."}
        required = list(item_schema.get("required") or [])
        if (self.note or self.report) and isinstance(properties, dict):
            meanings = ["One short sentence: why this action."] if self.note else []
            if self.report:
                meanings.append(f"For {REPORT_ACTION}: exactly what you saw - the control or screen "
                                "problem, what you did, what you expected and what happened.")
            properties["note"] = {"type": "string", "description": " ".join(meanings)}
            if self.note and "note" not in required:
                required.append("note")
        if self.duration_policy == "required" and "duration" not in required:
            required.append("duration")
        item_schema["required"] = required

        if self.max_actions == 1:
            return item_schema
        return {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": item_schema,
                    "minItems": 1,
                    "maxItems": self.max_actions,
                }
            },
            "required": ["actions"],
            "additionalProperties": False,
        }


def flatten_action_payload(raw: object) -> list[Action]:
    """Extract raw action objects without applying a contract."""
    if isinstance(raw, Mapping) and isinstance(raw.get("actions"), (list, tuple)):
        return [dict(item) for item in raw["actions"] if isinstance(item, Mapping)]
    if isinstance(raw, Mapping):
        return [dict(raw)]
    if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes, bytearray)):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []


def _copy_schema(value: Mapping[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, Mapping):
            copied[str(key)] = _copy_schema(item)
        elif isinstance(item, list):
            copied[str(key)] = [
                _copy_schema(element) if isinstance(element, Mapping) else element
                for element in item
            ]
        else:
            copied[str(key)] = item
    return copied


__all__ = [
    "ACTION_OPTIONS",
    "Action",
    "ActionContract",
    "ActionContractError",
    "ActionPlan",
    "CANONICAL_ACTIONS",
    "DEFAULT_ACTION_DURATION",
    "DurationPolicy",
    "KEY_ACTIONS",
    "KEY_ALIASES",
    "MAX_ACTION_DURATION_S",
    "MOUSE_ACTIONS",
    "MOUSE_BUTTONS",
    "PLAYWRIGHT_KEYS",
    "REPORT_ACTION",
    "NOTE_RULE",
    "NOTES_MAX_WORDS",
    "NOTES_RULE",
    "REPORT_RULE",
    "TAP_DURATION_S",
    "flatten_action_payload",
    "normalize_key",
    "split_keys",
]
