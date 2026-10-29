"""Episode timeline: what the player did and when, read from raw inputs and states.

Only facts are recorded: key-state segments (long ones split, short neighbouring ones
grouped into ordered chunks), mouse presses with their positions, the decisions an
automated actor logged (a model's action, its validity and its thinking, at the time of
the screen it decided from), the task progress over time and the outcome. Durations
stay raw; codebooks, waits, weights and frames are later choices. Timelines are cached
per episode and parse parameters.

An automated actor's episode logs every input its executor injected (`executed_input`);
those are parsed instead of the events the page reported, which can miss inputs (a page
that cancels pointerdown never sees mousedown) or include ones nobody made (a page that
dispatches its own key events). Its mouse moves and scrolls come from the action each
input is tagged with, and how the page's events compare is kept as `capture`. Human
episodes only have the page's events.
"""

from __future__ import annotations

import json
import math
import os
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from typing import Any

from env.action_executor import ActionExecutor

from .raw import RawEpisode, number

CODE_TO_KEY = {
    "Space": "Space", "Enter": "Enter", "Backspace": "Backspace", "Escape": "Escape",
    "Tab": "Tab", "ShiftLeft": "Shift", "ShiftRight": "ShiftRight",
    "ControlLeft": "Control", "ControlRight": "ControlRight", "AltLeft": "Alt",
    "AltRight": "AltRight", "ArrowUp": "ArrowUp", "ArrowDown": "ArrowDown",
    "ArrowLeft": "ArrowLeft", "ArrowRight": "ArrowRight",
}
WASD_TO_ARROW = {"w": "ArrowUp", "a": "ArrowLeft", "s": "ArrowDown", "d": "ArrowRight"}
BUTTON_NAMES = {0: "left", 1: "middle", 2: "right"}
# How far apart (s) an injected input and the page's event for it may be. The page
# stamps an event when its handler runs, which a busy main thread delays (0.3 s seen).
CAPTURE_WINDOW_S = 0.5


def _key_aliases(validator: ActionExecutor) -> dict[str, str]:
    """WASD stands for the arrows in a game that only allows arrows."""
    return {source: target for source, target in WASD_TO_ARROW.items()
            if source not in validator.allowed_keys and target in validator.allowed_keys}


def _event_key(event: dict[str, Any], validator: ActionExecutor, aliases: dict[str, str]) -> str | None:
    code = str(event.get("code") or "")
    candidates = []
    if code.startswith("Key") and len(code) == 4:
        candidates.append(code[-1].lower())
    elif code in CODE_TO_KEY:
        candidates.append(CODE_TO_KEY[code])
    key = event.get("key")
    if isinstance(key, str) and key:
        candidates.append("Space" if key == " " else key)
    for candidate in candidates:
        raw_key = validator._normalize_key(candidate)
        if not raw_key:
            continue
        normalized = validator._coerce_allowed_key(aliases.get(raw_key, raw_key))
        if normalized:
            return normalized
    return None


def _key_segments(inputs: list[dict[str, Any]], validator: ActionExecutor, duration: float) -> list[dict]:
    """One segment per constant set of held keys (repeats ignored, resets release all)."""
    aliases = _key_aliases(validator)
    # press_keys sends its downs/ups sequentially. Their millisecond transport
    # gaps are not extra player actions. Use one timestamp for each edge of a
    # recorded chord, retaining its overlap (all keys are actually held then).
    chords: dict[int, list[dict]] = defaultdict(list)
    for event in inputs:
        if event.get("action") == "press_keys" and event.get("action_index") is not None:
            chords[int(event["action_index"])].append(event)
    edge_times = {}
    for action_index, events in chords.items():
        downs = [e for e in events if e.get("type") == "keydown"]
        ups = [e for e in events if e.get("type") == "keyup"]
        down_keys = {_event_key(e, validator, aliases) for e in downs}
        up_keys = {_event_key(e, validator, aliases) for e in ups}
        if downs and ups and None not in down_keys and down_keys == up_keys:
            start = max(number(e.get("t")) for e in downs)
            end = min(number(e.get("t")) for e in ups)
            if start < end:
                edge_times[action_index] = {"keydown": start, "keyup": end}
    changes = []
    for event in inputs:
        kind = event.get("type")
        order = int(event.get("index", event.get("sequence", -1)))
        if kind == "input_reset":
            changes.append((number(event.get("t")), order, "reset", ""))
        elif kind in {"keydown", "keyup"}:
            if kind == "keydown" and event.get("repeat") is True:
                continue
            key = _event_key(event, validator, aliases)
            if key:
                t = edge_times.get(event.get("action_index"), {}).get(kind, number(event.get("t")))
                changes.append((t, order, kind, key))
    active: set[str] = set()
    last_t = 0.0
    segments = []
    for t, _index, kind, key in sorted(changes):
        t = max(0.0, min(t, duration))
        if t > last_t and active:
            segments.append({"t_start": last_t, "t_end": t, "keys": sorted(active)})
        if kind == "reset":
            active.clear()
        elif kind == "keydown":
            active.add(key)
        else:
            active.discard(key)
        last_t = t
    if active and duration > last_t:
        segments.append({"t_start": last_t, "t_end": duration, "keys": sorted(active)})
    return segments


def _split_long(segment: dict, cap_s: float) -> list[dict]:
    start, end = segment["t_start"], segment["t_end"]
    if cap_s <= 0 or end - start <= cap_s + 1e-9:
        return [segment]
    pieces, cursor = [], start
    while cursor < end - 1e-9:
        nxt = min(end, cursor + cap_s)
        pieces.append({**segment, "t_start": cursor, "t_end": nxt})
        cursor = nxt
    return pieces


def _valid_keys(keys: list[str], validator: ActionExecutor) -> bool:
    action = ({"action": "press_key", "key": keys[0]} if len(keys) == 1
              else {"action": "press_keys", "keys": list(keys)})
    return bool(validator.inspect_action(action)["normalized_action"])


def _group_chunks(actions: list[dict], micro: dict[str, Any], validator: ActionExecutor) -> list[dict]:
    """Group neighbouring short key segments into one ordered chunk whose release steps
    are steps of their own (they are what separates two taps from one press)."""
    out, i = [], 0
    while i < len(actions):
        current = actions[i]
        if current["kind"] != "keys":
            out.append(current)
            i += 1
            continue
        group = [current]
        total = current["t_end"] - current["t_start"]
        j = i + 1
        while j < len(actions) and actions[j]["kind"] == "keys":
            nxt, prev = actions[j], group[-1]
            gap = nxt["t_start"] - prev["t_end"]
            seg = nxt["t_end"] - nxt["t_start"]
            if (gap > micro["gap_s"] or seg > micro["max_segment_s"]
                    or prev["t_end"] - prev["t_start"] > micro["max_segment_s"]
                    or len(group) + 1 > micro["max_steps"]
                    or total + max(gap, 0.0) + seg > micro["max_total_s"]):
                break
            total += max(gap, 0.0) + seg
            group.append(nxt)
            j += 1
        if len(group) == 1:
            out.append(current)
            i += 1
            continue
        steps, previous_end = [], None
        for item in group:
            if previous_end is not None and item["t_start"] - previous_end > 1e-4:
                steps.append({"keys": [], "t_start": previous_end, "t_end": item["t_start"]})
            steps.append({"keys": item["keys"], "t_start": item["t_start"], "t_end": item["t_end"]})
            previous_end = item["t_end"]
        check = {"action": "key_chunk", "release_after": True,
                 "steps": [{"keys": s["keys"], "duration": max(0.01, s["t_end"] - s["t_start"])}
                           for s in steps]}
        if validator.inspect_action(check)["normalized_action"]:
            out.append({"kind": "chunk", "t_start": group[0]["t_start"], "t_end": group[-1]["t_end"],
                        "steps": steps})
        else:
            out.extend(group)
        i = j
    return out


_BUTTON_MASKS = {0: 1, 1: 4, 2: 2}  # MouseEvent.button -> its bit in MouseEvent.buttons


def _button(event: dict[str, Any]) -> int | None:
    try:
        return int(event.get("button"))
    except (TypeError, ValueError):
        return None


def _button_events(inputs: list[dict[str, Any]]):
    """Mouse button presses, releases and moves as (kind, button, event). From pointer
    events when the recording has them: a page can cancel pointerdown, after which the
    browser sends it no mousedown or mouseup for that press, but the pointer events
    still arrive (a press or release while another button is held is a pointermove
    naming that button). Older recordings and injected inputs have mouse events."""
    pointer = any(event.get("type") == "pointerdown" for event in inputs)
    down, up, move = ("pointerdown", "pointerup", "pointermove") if pointer else ("mousedown", "mouseup", "mousemove")
    for event in inputs:
        kind = event.get("type")
        if kind == "input_reset":
            yield "reset", None, event
        elif kind == move:
            button = _button(event) if pointer else None
            if button in _BUTTON_MASKS:
                held = int(number(event.get("buttons"))) & _BUTTON_MASKS[button]
                yield ("down" if held else "up"), button, event
            else:
                yield "move", None, event
        elif kind == down:
            yield "down", _button(event), event
        elif kind == up:
            yield "up", _button(event), event


def _trusted(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The page's events without those the page dispatched itself (`is_trusted` false;
    older recordings do not say, and keep everything)."""
    return [event for event in inputs if event.get("is_trusted") is not False]


def _mouse_actions(inputs: list[dict[str, Any]], validator: ActionExecutor, tolerance_px: float) -> list[dict]:
    """Button presses, each with where it went down and came up; a drag moved more than
    the tolerance while held."""
    active: dict[int, dict] = {}
    paths: dict[int, list[tuple[float, float]]] = {}
    out = []
    for kind, button, event in _button_events(inputs):
        if kind == "reset":
            active.clear()
            paths.clear()
            continue
        if kind == "move":
            x, y = number(event.get("client_x"), math.nan), number(event.get("client_y"), math.nan)
            if math.isfinite(x) and math.isfinite(y):
                for held in active:
                    paths[held].append((x, y))
            continue
        if kind == "down" and button in BUTTON_NAMES:
            active[button] = event
            x, y = number(event.get("client_x"), math.nan), number(event.get("client_y"), math.nan)
            paths[button] = [(x, y)] if math.isfinite(x) and math.isfinite(y) else []
            continue
        if kind != "up" or button not in BUTTON_NAMES or button not in active:
            continue
        down = active.pop(button)
        path = paths.pop(button, [])
        start = number(down.get("t"))
        end = max(start, number(event.get("t"), start))
        x1, y1 = number(down.get("client_x"), math.nan), number(down.get("client_y"), math.nan)
        x2, y2 = number(event.get("client_x"), x1), number(event.get("client_y"), y1)
        if not (math.isfinite(x1) and math.isfinite(y1)):
            continue
        if math.isfinite(x2) and math.isfinite(y2):
            path = [*path, (x2, y2)]
        name = BUTTON_NAMES[button]
        drag = math.hypot(x2 - x1, y2 - y1) > tolerance_px
        probe = ({"action": "drag", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "button": name}
                 if drag else {"action": "click", "x": x1, "y": y1, "button": name})
        if not validator.inspect_action(probe)["normalized_action"]:
            continue
        out.append({"kind": "mouse", "t_start": start, "t_end": end, "button": name,
                    "x": x1, "y": y1, "x2": x2, "y2": y2, "drag": drag,
                    "path_steps": max(2, min(32, len(path) - 1))})
    return out


def _executed_events(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The inputs an automated actor's executor injected, as page events in time order,
    positions in whole pixels as a page reports them."""
    out = []
    for record in inputs:
        if record.get("type") == "executed_input":
            event = {key: value for key, value in record.items() if key not in ("type", "event")}
            event["type"] = record.get("event")
            for axis in ("client_x", "client_y"):
                if isinstance(event.get(axis), (int, float)):
                    event[axis] = float(round(event[axis]))
            out.append(event)
    return sorted(out, key=lambda e: (number(e.get("t")), int(e.get("index", 0))))


def _pointer_actions(executed: list[dict[str, Any]], validator: ActionExecutor) -> list[dict]:
    """Executed mouse moves and scrolls, one per action (a press carries its own
    position, so only moves of a mouse_move action count)."""
    by_action: dict[int, list[dict]] = defaultdict(list)
    for event in executed:
        if (event.get("action"), event.get("type")) in {("mouse_move", "mousemove"), ("scroll", "wheel")}:
            by_action[int(event.get("action_index", -1))].append(event)
    out = []
    for events in by_action.values():
        last = events[-1]
        if last["action"] == "mouse_move":
            kind = "move"
            probe = {"action": "mouse_move", "x": number(last.get("client_x")), "y": number(last.get("client_y"))}
        else:
            kind = "scroll"
            probe = {"action": "scroll", "delta_x": sum(number(e.get("delta_x")) for e in events),
                     "delta_y": sum(number(e.get("delta_y")) for e in events)}
        if not validator.inspect_action(probe)["normalized_action"]:
            continue
        out.append({"kind": kind, "t_start": number(events[0].get("t")), "t_end": number(last.get("t")),
                    **{key: value for key, value in probe.items() if key != "action"}})
    return out


def _unpaired(injected: list[tuple[float, str]], observed: list[tuple[float, str]],
              window: float) -> tuple[list[tuple[float, str]], int]:
    """Pair each injected input with the nearest unpaired page event of the same kind at
    most `window` s away; returns the injected inputs left over and how many page
    events were."""
    times: dict[str, list[float]] = defaultdict(list)
    for t, label in observed:
        times[label].append(t)
    for values in times.values():
        values.sort()
    used: dict[str, set[int]] = defaultdict(set)
    lonely = []
    for t, label in sorted(injected):
        candidates = times.get(label, [])
        best = None
        i = bisect_left(candidates, t - window)
        while i < len(candidates) and candidates[i] <= t + window:
            if i not in used[label] and (best is None or abs(candidates[i] - t) < abs(candidates[best] - t)):
                best = i
            i += 1
        if best is None:
            lonely.append((t, label))
        else:
            used[label].add(best)
    return lonely, sum(len(values) for values in times.values()) - sum(len(u) for u in used.values())


def _capture(executed: list[dict[str, Any]], inputs: list[dict[str, Any]], validator: ActionExecutor) -> dict[str, int]:
    """How the page's own events compare with what was injected: per kind (presses,
    keys the game allows, wheel turns) how many were injected, how many the page did
    not report (`unseen`; for presses also how many of those it reported only as a
    click) and how many it reported that nothing injected (`page_only`). Events the
    page marked untrusted (it dispatched them itself) are left out as parsing leaves
    them out, and counted as `page_untrusted`. Off counts mean that game's page-parsed
    episodes, which human recordings are, would be off too."""
    aliases = _key_aliases(validator)
    page = [e for e in _trusted(inputs) if e.get("type") not in ("executed_input", "agent_decision")]

    def presses(events: list[dict]) -> list[tuple[float, str]]:
        return [(number(e.get("t")), str(button)) for kind, button, e in _button_events(events) if kind == "down"]

    def keys(events: list[dict]) -> list[tuple[float, str]]:
        out = []
        for event in events:
            if event.get("type") == "keydown" and event.get("repeat") is not True:
                key = _event_key(event, validator, aliases)
                if key:
                    out.append((number(event.get("t")), key))
        return out

    def wheels(events: list[dict]) -> list[tuple[float, str]]:
        return [(number(e.get("t")), "wheel") for e in events if e.get("type") == "wheel"]

    counts: dict[str, int] = {"page_untrusted": sum(
        1 for e in inputs if e.get("is_trusted") is False
        and e.get("type") in ("keydown", "mousedown", "pointerdown", "click", "wheel"))}
    for name, pick in (("press", presses), ("key", keys), ("wheel", wheels)):
        injected = pick(executed)
        lonely, page_only = _unpaired(injected, pick(page), CAPTURE_WINDOW_S)
        counts[f"{name}_injected"] = len(injected)
        counts[f"{name}_unseen"] = len(lonely)
        counts[f"{name}_page_only"] = page_only
        if name == "press":
            releases = sorted(number(e.get("t")) for e in executed if e.get("type") == "mouseup")
            clicks = sorted(number(e.get("t")) for e in page if e.get("type") == "click")
            seen_as_click = 0
            for t, _button in lonely:
                j = bisect_left(releases, t)
                up = releases[j] if j < len(releases) else t
                i = bisect_left(clicks, up - CAPTURE_WINDOW_S)
                if i < len(clicks) and clicks[i] <= up + CAPTURE_WINDOW_S:
                    seen_as_click += 1
            counts["press_seen_only_as_click"] = seen_as_click
    return counts


def _decisions(inputs: list[dict[str, Any]], validator: ActionExecutor) -> list[dict]:
    """The decisions an automated actor logged: the screen time it decided from
    (older recordings only have the execution time, which follows it closely when
    inference pauses the game), its actions as the executor ran them (normalized: keys,
    aliases and default step durations resolved; a rejected action as logged), whether
    the executor accepted each and the thinking that came with them."""
    out = []
    for event in inputs:
        if event.get("type") != "agent_decision":
            continue
        actions = [dict(action) for action in event.get("actions") or [] if isinstance(action, dict)]
        valid = event.get("valid")
        if not isinstance(valid, list) or len(valid) != len(actions):
            valid = [validator.inspect_action(action)["is_valid"] for action in actions]
        actions = [validator.inspect_action(action)["normalized_action"] or action for action in actions]
        note = str(event.get("note") or "").strip()
        thinking = str(event.get("thinking") or "").strip()
        t = number(event.get("t"))
        out.append({"t": number(event.get("observation_t"), t), "t_executed": t, "actions": actions,
                    "valid": [bool(v) for v in valid], "thought": thinking, "note": note})
    return out


def _state_progress(row: dict[str, Any]) -> float:
    state = row.get("state") or {}
    task = row.get("task") or state.get("task") or {}
    metrics = task.get("metrics") or {}
    for value in (task.get("progress"), task.get("score_best"),
                  metrics.get("progress_best"), metrics.get("progress")):
        if value is not None:
            return number(value)
    return 0.0


def _parse_actions(events: list[dict[str, Any]], validator: ActionExecutor, parse: dict[str, Any],
                   duration: float, allow_clicks: bool, pointer: list[dict] = ()) -> list[dict]:
    """Key segments and mouse presses from page-style events (plus `pointer` moves and
    scrolls), short key segments grouped; any mouse action ends a group."""
    keys = []
    for segment in _key_segments(events, validator, duration):
        for piece in _split_long(segment, float(parse["long_hold_split_s"])):
            if _valid_keys(piece["keys"], validator):
                keys.append({"kind": "keys", **piece})
    mouse = (_mouse_actions(events, validator, float(parse["click_move_tolerance_px"])) + list(pointer)
             if allow_clicks else [])
    actions = sorted([*keys, *mouse], key=lambda item: item["t_start"])
    return sorted(_group_chunks(actions, parse["micro_chunk"], validator), key=lambda a: a["t_start"])


def build_timeline(episode: RawEpisode, parse: dict[str, Any]) -> dict[str, Any]:
    inputs = episode.load_inputs()
    states = sorted(episode.load_states(), key=lambda row: number(row.get("t")))
    summary_duration = number(json.loads((episode.path / "summary.json").read_text())
                              .get("timing", {}).get("duration_s"))
    duration = max([summary_duration, *(number(e.get("t")) for e in inputs)] or [0.0])
    validator = ActionExecutor(None, episode.controls_namespace())  # type: ignore[arg-type]
    allow_clicks = episode.controls["allow_clicks"]
    executed = _executed_events(inputs)
    actions = _parse_actions(executed or _trusted(inputs), validator, parse, duration, allow_clicks,
                             _pointer_actions(executed, validator))
    capture = None
    if executed:
        # What parsing the page's events alone (as for a human recording) would give.
        capture = _capture(executed, inputs, validator)
        from_page = _parse_actions(_trusted(inputs), validator, parse, duration, allow_clicks)
        for kind in ("keys", "chunk", "mouse"):
            capture[f"parsed_{kind}_injected"] = sum(1 for a in actions if a["kind"] == kind)
            capture[f"parsed_{kind}_page"] = sum(1 for a in from_page if a["kind"] == kind)
    return {
        "episode_id": episode.episode_id,
        "game_id": episode.game_id,
        "task_id": episode.task_id,
        "outcome": episode.outcome,
        "duration_s": duration,
        "video_offset_s": episode.video_offset_s,
        "video_duration_s": episode.video_duration_s,
        "hold_duration_s": episode.controls["hold_duration"],
        "actions": actions,
        "decisions": _decisions(inputs, validator),
        "input_source": "executed" if executed else "page",
        "capture": capture,
        "states": {"t": [number(row.get("t")) for row in states],
                   "progress": [_state_progress(row) for row in states]},
    }


def load_timeline(episode: RawEpisode, parse: dict[str, Any], cache_dir: Path) -> dict[str, Any]:
    path = cache_dir / episode.game_id / f"{episode.episode_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    timeline = build_timeline(episode, parse)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(timeline), encoding="utf-8")
    tmp.replace(path)
    return timeline
