"""Translate structured agent commands into Playwright actions."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from playwright.async_api import Page

from harnesses.actions import (
    CANONICAL_ACTIONS,
    DEFAULT_ACTION_DURATION,
    KEY_ACTIONS,
    MAX_ACTION_DURATION_S,
    MOUSE_ACTIONS,
    MOUSE_BUTTONS,
    PLAYWRIGHT_KEYS,
    TAP_DURATION_S,
    normalize_key,
    split_keys,
)

if TYPE_CHECKING:
    from catalog.games._base import RoleControls

LOGGER = logging.getLogger(__name__)

ActionDict = dict[str, Any]
InputReport = Callable[[dict[str, Any]], None]

_BUTTON_CODES = {"left": 0, "middle": 1, "right": 2}


class _ReportingMouse:
    """The page mouse, reporting each input it injects as the page event it causes
    (DOM field names, so the injected stream reads like a recorded one)."""

    def __init__(self, mouse: Any, report: InputReport):
        self._mouse = mouse
        self._report = report
        self._x: float | None = None
        self._y: float | None = None

    def _moved(self, x: float, y: float, steps: int = 1) -> None:
        # Playwright moves in `steps` equal increments from where the pointer was.
        if self._x is not None and self._y is not None:
            for step in range(1, steps):
                self._report({"event": "mousemove", "client_x": self._x + (x - self._x) * step / steps,
                              "client_y": self._y + (y - self._y) * step / steps})
        self._report({"event": "mousemove", "client_x": x, "client_y": y})
        self._x, self._y = x, y

    def _button(self, event: str, button: str) -> None:
        self._report({"event": event, "button": _BUTTON_CODES.get(button, 0),
                      "client_x": self._x, "client_y": self._y})

    async def move(self, x: float, y: float, **kwargs: Any) -> None:
        self._moved(float(x), float(y), int(kwargs.get("steps") or 1))
        await self._mouse.move(x, y, **kwargs)

    async def down(self, **kwargs: Any) -> None:
        self._button("mousedown", kwargs.get("button", "left"))
        await self._mouse.down(**kwargs)

    async def up(self, **kwargs: Any) -> None:
        self._button("mouseup", kwargs.get("button", "left"))
        await self._mouse.up(**kwargs)

    async def click(self, x: float, y: float, **kwargs: Any) -> None:
        self._moved(float(x), float(y))
        button = kwargs.get("button", "left")
        self._button("mousedown", button)
        self._button("mouseup", button)
        await self._mouse.click(x, y, **kwargs)

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        self._report({"event": "wheel", "delta_x": float(delta_x), "delta_y": float(delta_y),
                      "client_x": self._x, "client_y": self._y})
        await self._mouse.wheel(delta_x, delta_y)


class _ReportingKeyboard:
    """The page keyboard, reporting each key it presses or releases."""

    def __init__(self, keyboard: Any, report: InputReport):
        self._keyboard = keyboard
        self._report = report

    async def down(self, key: str, **kwargs: Any) -> None:
        self._report({"event": "keydown", "key": key})
        await self._keyboard.down(key, **kwargs)

    async def up(self, key: str, **kwargs: Any) -> None:
        self._report({"event": "keyup", "key": key})
        await self._keyboard.up(key, **kwargs)

    async def press(self, key: str, **kwargs: Any) -> None:
        self._report({"event": "keydown", "key": key})
        self._report({"event": "keyup", "key": key})
        await self._keyboard.press(key, **kwargs)

    async def type(self, text: str, **kwargs: Any) -> None:
        for character in text:
            self._report({"event": "keydown", "key": character})
            self._report({"event": "keyup", "key": character})
        await self._keyboard.type(text, **kwargs)


class _ReportingPage:
    def __init__(self, page: Any, report: InputReport):
        self._page = page
        self.mouse = _ReportingMouse(page.mouse, report)
        self.keyboard = _ReportingKeyboard(page.keyboard, report)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)


class ActionExecutor:
    """Executes normalized JSON-like commands on a Playwright page.

    With `on_input`, every low-level input the executor injects is reported first, in
    page-event form and tagged with the action it belongs to: what an automated actor
    really did, whatever the page lets a recorder observe.
    """

    def __init__(
        self,
        page: Page,
        controls: RoleControls | None,
        on_input: InputReport | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ):
        # Synchronous simulators advance their clock here instead of wall time.
        self._sleep_override = sleep
        self._on_input = on_input
        self._action_tag: dict[str, Any] = {}
        self._actions_run = 0
        self.page = _ReportingPage(page, self._report_input) if on_input is not None else page
        self.controls = controls or SimpleNamespace(
            allowed_keys=set(),
            hold_duration=DEFAULT_ACTION_DURATION,
            allow_clicks=True,
        )

        self.allowed_keys = self._normalize_key_set(self.controls.allowed_keys)
        self.allow_clicks = bool(self.controls.allow_clicks)
        self.hold_duration = self._coerce_duration(
            getattr(self.controls, "hold_duration", DEFAULT_ACTION_DURATION),
            default=DEFAULT_ACTION_DURATION,
        )
        self._pressed_keys: set[str] = set()

    async def _sleep(self, seconds: float) -> None:
        await (self._sleep_override or asyncio.sleep)(seconds)

    def inspect_action(self, raw: Any) -> dict[str, Any]:
        """Return whether a low-level action payload is executable under current controls."""
        normalized = self._parse_action(raw)
        if normalized is not None:
            return {
                "is_valid": True,
                "reason": "valid",
                "invalid_kind": None,
                "normalized_action": normalized,
            }

        if not isinstance(raw, dict):
            return {
                "is_valid": False,
                "reason": "invalid_payload",
                "invalid_kind": "no_function_call",
                "normalized_action": None,
            }

        action_type = str(raw.get("action", "")).strip().lower()
        if not action_type:
            return {
                "is_valid": False,
                "reason": "missing_action",
                "invalid_kind": "no_function_call",
                "normalized_action": None,
            }

        # mouse_move runs in every game; the other mouse actions need allow_clicks.
        if action_type in MOUSE_ACTIONS - {"mouse_move"} and not self.allow_clicks:
            reason = "mouse_action_not_allowed"
        elif action_type == "key_chunk" and not (raw.get("steps") or raw.get("chunk") or raw.get("actions")):
            reason = "key_chunk_requires_nonempty_steps"
        elif action_type in KEY_ACTIONS:
            reason = "malformed_key_payload"
        elif action_type not in CANONICAL_ACTIONS:
            reason = "unsupported_action_type"
        else:
            reason = "malformed_action_payload"

        return {
            "is_valid": False,
            "reason": reason,
            "invalid_kind": "out_of_space",
            "normalized_action": None,
        }

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _coerce_duration(value: Any, default: float) -> float:
        try:
            duration = float(default if value is None else value)
        except (TypeError, ValueError):
            duration = float(default)
        return min(MAX_ACTION_DURATION_S, max(0.0, duration))

    @staticmethod
    def _normalize_mouse_button(value: Any, default: str = "left") -> str | None:
        button = str(default if value is None else value).strip().lower()
        return button if button in MOUSE_BUTTONS else None

    def _normalize_key(self, key: str) -> str | None:
        """Normalize common key spellings to Playwright-compatible names."""
        return normalize_key(key)

    def _normalize_key_set(self, keys: Iterable[str] | None) -> set[str]:
        normalized: set[str] = set()
        for key in keys or ():
            normalized_key = self._normalize_key(key)
            if normalized_key:
                normalized.add(normalized_key)
        return normalized


    def _coerce_allowed_key(self, key: str) -> str | None:
        """Normalize a key Playwright can press. Catalog allowed_keys are descriptive, not enforced."""
        normalized = self._normalize_key(key)
        return normalized if normalized in PLAYWRIGHT_KEYS else None

    @staticmethod
    def _split_keys(raw_keys: str) -> list[str]:
        return split_keys(raw_keys)

    @classmethod
    def _copy_action(cls, raw: dict[str, Any], action_type: str) -> ActionDict:
        action = dict(raw)
        action["action"] = action_type
        return action

    @classmethod
    def _numeric_fields(cls, raw: dict[str, Any], *names: str) -> dict[str, float] | None:
        values: dict[str, float] = {}
        for name in names:
            value = raw.get(name)
            if not cls._is_number(value):
                return None
            values[name] = float(value)
        return values

    def _parse_click_action(
        self,
        raw: dict[str, Any],
        *,
        action_type: str,
        forced_button: str | None = None,
    ) -> ActionDict | None:
        coords = self._numeric_fields(raw, "x", "y")
        if not coords or not self.allow_clicks:
            return None

        button = self._normalize_mouse_button(forced_button or raw.get("button"), default="left")
        if not button:
            return None

        action = self._copy_action(raw, action_type)
        action.update(coords)
        action["button"] = button
        if action_type == "click_hold":
            action["duration"] = self._coerce_duration(
                raw.get("duration"),
                self.hold_duration,
            )
        elif "duration" in raw:
            action["duration"] = self._coerce_duration(raw.get("duration"), self.hold_duration)
        return action

    def _parse_mouse_move(self, raw: dict[str, Any]) -> ActionDict | None:
        coords = self._numeric_fields(raw, "x", "y")
        if not coords:
            return None

        action = self._copy_action(raw, "mouse_move")
        action.update(coords)
        origin = self._numeric_fields(raw, "from_x", "from_y")
        if origin:
            action.update(origin)
        if "duration" in raw:
            action["duration"] = self._coerce_duration(raw.get("duration"), self.hold_duration)
        return action

    def _parse_drag(self, raw: dict[str, Any]) -> ActionDict | None:
        coords = self._numeric_fields(raw, "x1", "y1", "x2", "y2")
        if not coords or not self.allow_clicks:
            return None

        button = self._normalize_mouse_button(raw.get("button"), default="left")
        if not button:
            return None

        action = self._copy_action(raw, "drag")
        action.update(coords)
        action["button"] = button
        action["duration"] = self._coerce_duration(raw.get("duration"), self.hold_duration)
        steps = raw.get("steps")
        if steps is not None:
            try:
                action["steps"] = max(1, int(steps))
            except (TypeError, ValueError):
                return None
        return action

    def _parse_scroll(self, raw: dict[str, Any]) -> ActionDict | None:
        if not self.allow_clicks:
            return None

        delta_x = raw.get("delta_x", 0)
        delta_y = raw.get("delta_y", 0)
        if not self._is_number(delta_x) or not self._is_number(delta_y):
            return None

        action = self._copy_action(raw, "scroll")
        action["delta_x"] = float(delta_x)
        action["delta_y"] = float(delta_y)
        if "duration" in raw:
            action["duration"] = self._coerce_duration(raw.get("duration"), self.hold_duration)
        return action

    def _parse_type(self, raw: dict[str, Any]) -> ActionDict | None:
        text = raw.get("text")
        if text is None:
            return None
        text = str(text)
        if not text:
            return None

        action = self._copy_action(raw, "type")
        action["text"] = text
        action["duration"] = self._coerce_duration(raw.get("duration"), 1.0)
        coords = self._numeric_fields(raw, "x", "y")
        if coords:
            action.update(coords)
        if "press_enter" in raw:
            action["press_enter"] = bool(raw.get("press_enter"))
        return action

    def _parse_press_key(self, raw: dict[str, Any]) -> ActionDict | None:
        key = raw.get("key")
        if not isinstance(key, str):
            return None

        key_parts = self._split_keys(key)
        if not key_parts:
            return None
        if len(key_parts) > 1:
            combo_action = self._copy_action(raw, "press_keys")
            combo_action["keys"] = key_parts
            combo_action.pop("key", None)
            return self._parse_press_keys(combo_action)

        normalized_key = self._coerce_allowed_key(key_parts[0])
        if not normalized_key:
            return None

        action = self._copy_action(raw, "press_key")
        action["key"] = normalized_key
        if "duration" in raw:
            action["duration"] = self._coerce_duration(raw.get("duration"), self.hold_duration)
        return action

    def _parse_press_keys(self, raw: dict[str, Any]) -> ActionDict | None:
        keys = raw.get("keys")
        if isinstance(keys, str):
            keys = self._split_keys(keys)
        if not isinstance(keys, (list, tuple)) or not keys:
            return None

        normalized_keys: list[str] = []
        for key in keys:
            normalized_key = self._coerce_allowed_key(str(key))
            if not normalized_key:
                return None
            normalized_keys.append(normalized_key)

        action = self._copy_action(raw, "press_keys")
        action["keys"] = normalized_keys
        if "duration" in raw:
            action["duration"] = self._coerce_duration(raw.get("duration"), self.hold_duration)
        return action

    def _parse_set_keys(self, raw: dict[str, Any]) -> ActionDict | None:
        keys = raw.get("keys", [])
        if isinstance(keys, str):
            keys = self._split_keys(keys)
        if keys is None:
            keys = []
        if not isinstance(keys, (list, tuple)):
            return None

        normalized_keys: list[str] = []
        seen: set[str] = set()
        for key in keys:
            normalized_key = self._coerce_allowed_key(str(key))
            if not normalized_key:
                return None
            if normalized_key not in seen:
                normalized_keys.append(normalized_key)
                seen.add(normalized_key)

        action = self._copy_action(raw, "set_keys")
        action["keys"] = normalized_keys
        if "duration" in raw:
            action["duration"] = self._coerce_duration(
                raw.get("duration"),
                self.hold_duration,
            )
        return action

    def _parse_key_chunk(self, raw: dict[str, Any]) -> ActionDict | None:
        """A short ordered run of key states, each held for its own span.

        Every step is a `set_keys` target, so an empty `keys` is a real step that
        lets go: "press A, release, press A again" is three steps, not two, and the
        release is what keeps it from collapsing into one long hold.
        """
        steps_raw = raw.get("steps") or raw.get("chunk") or raw.get("actions")
        if not isinstance(steps_raw, (list, tuple)) or not steps_raw:
            return None

        steps: list[ActionDict] = []
        for item in steps_raw:
            if not isinstance(item, dict):
                return None
            step_keys = item.get("keys", [])
            if "key" in item and not step_keys:
                step_keys = item.get("key")
            parsed = self._parse_set_keys(
                {
                    "action": "set_keys",
                    "keys": step_keys,
                    "duration": item.get("duration"),
                }
            )
            if not parsed:
                return None
            steps.append({"keys": list(parsed["keys"]), "duration": parsed["duration"]})

        action = self._copy_action(raw, "key_chunk")
        action["steps"] = steps
        action["release_after"] = bool(raw.get("release_after", True))
        action.pop("chunk", None)
        action.pop("actions", None)
        return action

    def _parse_wait(self, raw: dict[str, Any]) -> ActionDict:
        action = self._copy_action(raw, "wait")
        action["duration"] = self._coerce_duration(raw.get("duration"), self.hold_duration)
        return action

    def _parse_action(self, raw: dict[str, Any] | None) -> ActionDict | None:
        """Validate and normalize low-level actions before execution."""
        if not raw or not isinstance(raw, dict):
            return None

        action_type = str(raw.get("action", "")).strip().lower()
        if not action_type:
            return None

        parsers = {
            "click": lambda action: self._parse_click_action(action, action_type="click"),
            "click_hold": lambda action: self._parse_click_action(action, action_type="click_hold"),
            "mouse_move": self._parse_mouse_move,
            "drag": self._parse_drag,
            "scroll": self._parse_scroll,
            "type": self._parse_type,
            "press_key": self._parse_press_key,
            "press_keys": self._parse_press_keys,
            "set_keys": self._parse_set_keys,
            "key_chunk": self._parse_key_chunk,
            "wait": self._parse_wait,
        }
        parser = parsers.get(action_type)
        if not parser:
            return None
        return parser(raw)

    def _resolve_mouse_action_duration(self, action: ActionDict) -> float:
        return self._coerce_duration(action.get("duration"), TAP_DURATION_S)

    def _resolve_key_hold_duration(self, action: ActionDict) -> float:
        explicit_duration = action.get("duration")
        if explicit_duration is not None:
            return self._coerce_duration(explicit_duration, self.hold_duration)

        return TAP_DURATION_S

    async def _execute_click(self, action: ActionDict) -> None:
        button = action.get("button", "left")
        LOGGER.info("Executing Action: click(%s, %s) button=%s", action["x"], action["y"], button)
        await self.page.mouse.click(action["x"], action["y"], button=button)
        await self._sleep(self._resolve_mouse_action_duration(action))

    async def _execute_click_hold(self, action: ActionDict) -> None:
        hold_seconds = self._resolve_mouse_action_duration(action)
        button = action.get("button", "left")
        LOGGER.info(
            "Executing Action: click_hold(%s, %s) button=%s hold=%.3f",
            action["x"],
            action["y"],
            button,
            hold_seconds,
        )
        await self.page.mouse.move(action["x"], action["y"])
        await self.page.mouse.down(button=button)
        await self._sleep(hold_seconds)
        await self.page.mouse.up(button=button)

    async def _execute_type(self, action: ActionDict) -> None:
        duration = self._coerce_duration(action.get("duration"), 1.0)
        LOGGER.info(
            "Executing Action: keyboard type text(%s) duration=%.3f",
            action["text"],
            duration,
        )
        text = action["text"]
        press_enter = bool(action.get("press_enter"))

        if "x" in action and "y" in action:
            await self.page.mouse.click(action["x"], action["y"])

        stroke_count = len(text) + (1 if press_enter else 0)
        per_stroke_delay = (duration / stroke_count) if stroke_count > 0 else 0.0

        async def type_stroke(character: str) -> None:
            if character in ("\n", "\r"):
                await self.page.keyboard.press("Enter")
            elif character == "\b":
                await self.page.keyboard.press("Backspace")
            else:
                await self.page.keyboard.type(character)
            if per_stroke_delay > 0:
                await self._sleep(per_stroke_delay)

        for character in text:
            await type_stroke(character)

        if press_enter:
            await type_stroke("\n")

    async def release_all_keys(self) -> None:
        """Release sticky keys and clear the executor's keyboard state."""
        if not self._pressed_keys:
            return
        for key in sorted(self._pressed_keys, reverse=True):
            try:
                await self.page.keyboard.up(key)
            except Exception:
                LOGGER.debug("Failed to release key %s", key, exc_info=True)
        self._pressed_keys.clear()

    async def _transition_keys(self, target_keys: Iterable[str]) -> None:
        target = {str(key) for key in target_keys}
        for key in sorted(self._pressed_keys - target):
            await self.page.keyboard.up(key)
        for key in sorted(target - self._pressed_keys):
            await self.page.keyboard.down(key)
        self._pressed_keys = target

    async def _execute_set_keys(self, action: ActionDict) -> None:
        keys = action.get("keys") or []
        LOGGER.info("Executing Action: set_keys keys=%s", keys)
        await self._transition_keys(keys)
        if "duration" in action:
            duration = self._coerce_duration(action.get("duration"), self.hold_duration)
            if duration > 0:
                await self._sleep(duration)

    async def _execute_key_chunk(self, action: ActionDict) -> None:
        steps = action.get("steps") or []
        LOGGER.info("Executing Action: key_chunk steps=%s", len(steps))
        for step in steps:
            await self._execute_set_keys(
                {
                    "action": "set_keys",
                    "keys": list(step.get("keys") or []),
                    "duration": step.get("duration"),
                }
            )
        if bool(action.get("release_after", True)):
            await self._transition_keys([])

    async def _execute_press_key(self, action: ActionDict) -> None:
        key = action.get("key", "")
        if not key:
            return

        hold_seconds = self._resolve_key_hold_duration(action)
        LOGGER.info("Executing Action: press_key key=%s hold=%.3f", key, hold_seconds)
        await self._transition_keys([key])
        await self._sleep(hold_seconds)
        await self._transition_keys([])

    async def _execute_press_keys(self, action: ActionDict) -> None:
        keys = action["keys"]
        hold_seconds = self._resolve_key_hold_duration(action)
        LOGGER.info("Executing Action: press_keys keys=%s hold=%.3f", keys, hold_seconds)

        await self._transition_keys(keys)
        await self._sleep(hold_seconds)
        await self._transition_keys([])

    async def _execute_scroll(self, action: ActionDict) -> None:
        LOGGER.info("Executing Action: scroll(%s, %s)", action["delta_x"], action["delta_y"])
        await self.page.mouse.wheel(action["delta_x"], action["delta_y"])
        await self._sleep(self._resolve_mouse_action_duration(action))

    async def _execute_mouse_move(self, action: ActionDict) -> None:
        LOGGER.info("Executing Action: mouse_move(%s, %s)", action["x"], action["y"])
        if "from_x" in action and "from_y" in action:
            await self.page.mouse.move(action["from_x"], action["from_y"])
        await self.page.mouse.move(action["x"], action["y"])
        await self._sleep(self._resolve_mouse_action_duration(action))

    async def _execute_drag(self, action: ActionDict) -> None:
        button = action.get("button", "left")
        x1 = float(action["x1"])
        y1 = float(action["y1"])
        x2 = float(action["x2"])
        y2 = float(action["y2"])
        steps = max(1, int(action.get("steps", 10)))
        duration = self._coerce_duration(action.get("duration"), self.hold_duration)

        LOGGER.info(
            "Executing Action: drag(%s, %s) to (%s, %s) button=%s",
            x1,
            y1,
            x2,
            y2,
            button,
        )
        await self.page.mouse.move(x1, y1)
        await self.page.mouse.down(button=button)

        if duration > 0:
            # Step k is due at k/steps of the duration. A move waits for the page (tens
            # of ms in a busy game), so a late step skips ahead to where the pointer
            # should be by now instead of stretching the drag.
            start = time.monotonic()
            step = 0
            while step < steps:
                due_step = int((time.monotonic() - start) / duration * steps) + 1
                step = min(steps, max(step + 1, due_step))
                await self.page.mouse.move(x1 + (x2 - x1) * (step / steps),
                                           y1 + (y2 - y1) * (step / steps))
                remaining = start + duration * step / steps - time.monotonic()
                if remaining > 0:
                    await self._sleep(remaining)
        else:
            await self.page.mouse.move(x2, y2, steps=steps)

        await self.page.mouse.up(button=button)

    async def _execute_wait(self, action: ActionDict) -> None:
        duration = self._coerce_duration(action.get("duration"), self.hold_duration)
        LOGGER.info("Executing Action: wait(%.3f)", duration)
        await self._sleep(duration)

    async def execute(self, action: ActionDict) -> None:
        """Execute a single action dictionary."""
        if not action:
            return

        raw_action = dict(action)
        normalized_action = self._parse_action(action)
        if not normalized_action:
            LOGGER.warning("Ignoring invalid/disallowed action payload: %s", raw_action)
            return

        handlers = {
            "click": self._execute_click,
            "click_hold": self._execute_click_hold,
            "type": self._execute_type,
            "press_key": self._execute_press_key,
            "press_keys": self._execute_press_keys,
            "set_keys": self._execute_set_keys,
            "key_chunk": self._execute_key_chunk,
            "scroll": self._execute_scroll,
            "mouse_move": self._execute_mouse_move,
            "drag": self._execute_drag,
            "wait": self._execute_wait,
        }
        action_type = normalized_action["action"]
        handler = handlers.get(action_type)
        if not handler:
            LOGGER.info("Ignoring unsupported action type: %s", action_type)
            return
        self._action_tag = {"action": action_type, "action_index": self._actions_run}
        self._actions_run += 1
        try:
            await handler(normalized_action)
        finally:
            self._action_tag = {}

    def _report_input(self, event: dict[str, Any]) -> None:
        if self._on_input is None:
            return
        try:
            self._on_input({**event, **self._action_tag})
        except Exception:  # logging must never leave an input half-executed
            LOGGER.warning("Could not log an injected input", exc_info=True)

    async def execute_actions(self, actions: Iterable[ActionDict]) -> None:
        for action in actions:
            await self.execute(action)
