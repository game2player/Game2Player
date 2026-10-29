"""From a timeline to the decision sequence a model is trained on.

Each entry is one decision at one moment, seen on the frame at that moment: an action
(labelled with codebook durations) or a wait. Where the episode logged the actor's
decisions (a model playing through the evaluation harness), those decisions are the
entries; otherwise actions come from the parsed key and mouse events and waits from a
wait policy. Codebooks and wait policies are looked up by name, so another one is a new
function plus a registry entry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from harnesses.actions import TAP_DURATION_S

from .raw import number

# Actions whose duration is how long something is held or waited for. Other actions
# are labelled without one (a click's or mouse move's duration is only a pause after it).
HELD = frozenset({"press_key", "press_keys", "set_keys", "click_hold", "drag", "wait"})
# Held actions that last the game's hold_duration when they name no duration.
HOLD_BY_DEFAULT = frozenset({"click_hold", "drag", "wait"})


class Codes:
    """Durations are snapped to the nearest code; presses shorter than `tap_below_s`
    are taps (no duration; the executor taps for TAP_DURATION_S)."""

    def __init__(self, codes: list[float], tap_below_s: float):
        self.codes = sorted(float(code) for code in codes)
        self.tap_below_s = float(tap_below_s)
        self.tap_executed_s = TAP_DURATION_S

    def snap(self, seconds: float, codes: list[float] | None = None) -> float:
        return min(codes or self.codes, key=lambda code: abs(code - seconds))

    def label(self, action: dict[str, Any]) -> tuple[dict[str, Any], float]:
        """The action label in executor form and how long the executor runs it."""
        kind = action["kind"]
        raw = action["t_end"] - action["t_start"]
        if kind == "keys":
            keys = action["keys"]
            label = ({"action": "press_key", "key": keys[0]} if len(keys) == 1
                     else {"action": "press_keys", "keys": list(keys)})
            if raw < self.tap_below_s:
                return label, self.tap_executed_s
            label["duration"] = self.snap(raw)
            return label, label["duration"]
        if kind == "chunk":
            steps = [{"keys": list(step["keys"]), "duration": self.snap(step["t_end"] - step["t_start"])}
                     for step in action["steps"]]
            return ({"action": "key_chunk", "steps": steps, "release_after": True},
                    sum(step["duration"] for step in steps))
        if kind == "mouse":
            if action["drag"]:
                duration = self.snap(max(raw, self.tap_below_s))
                return ({"action": "drag", "x1": action["x"], "y1": action["y"], "x2": action["x2"],
                         "y2": action["y2"], "button": action["button"], "duration": duration,
                         "steps": action["path_steps"]}, duration)
            if raw < self.tap_below_s:
                return ({"action": "click", "x": action["x"], "y": action["y"],
                         "button": action["button"]}, self.tap_executed_s)
            duration = self.snap(raw)
            return ({"action": "click_hold", "x": action["x"], "y": action["y"],
                     "button": action["button"], "duration": duration}, duration)
        if kind == "move":
            return {"action": "mouse_move", "x": action["x"], "y": action["y"]}, self.tap_executed_s
        if kind == "scroll":
            return ({"action": "scroll", "delta_x": action["delta_x"], "delta_y": action["delta_y"]},
                    self.tap_executed_s)
        raise ValueError(f"unknown timeline action kind {kind!r}")

    def recorded(self, action: dict[str, Any], hold_s: float,
                 wait_codes: list[float] | None = None) -> tuple[dict[str, Any], float]:
        """A logged model action as a label, and how long the executor ran it: the same
        codes and taps as parsed actions, an omitted hold made explicit, and waits on
        `wait_codes` (the codes plus the wait policy's look-again time) as parsed waits are."""
        label = {key: value for key, value in action.items() if key != "duration"}
        kind = label["action"]
        if kind == "key_chunk":
            steps = [step for step in action.get("steps") or [] if isinstance(step, dict)]
            durations = [number(step.get("duration"), hold_s) for step in steps]
            label["steps"] = [{"keys": list(step.get("keys") or []), "duration": self.snap(duration)}
                              for step, duration in zip(steps, durations)]
            return label, sum(durations)
        if kind not in HELD:
            return label, float(action.get("duration", 1.0 if kind == "type" else TAP_DURATION_S))
        if "duration" in action:
            raw = float(action["duration"])
        elif kind in HOLD_BY_DEFAULT:
            raw = float(hold_s)
        else:
            return label, (TAP_DURATION_S if kind != "set_keys" else 0.0)
        if kind in {"press_key", "press_keys"} and raw < self.tap_below_s:
            return label, raw
        label["duration"] = self.snap(raw, wait_codes if kind == "wait" else None)
        return label, raw

    def describe(self) -> dict[str, Any]:
        return {"codes": self.codes, "tap_below_s": self.tap_below_s, "tap_executed_s": self.tap_executed_s}


CODEBOOKS: dict[str, Callable[..., Codes]] = {"codes": Codes}


def closed_loop_waits(actions: list[dict], codebook: Codes, look_again_s: float,
                      micro_pause_s: float) -> list[list[tuple[float, float]]]:
    """For each gap after an action: waits of at most `look_again_s` covering the idle
    time from the end of the executed action to the next action; a last slack of at
    most `micro_pause_s` is not a wait. Returns (start, duration) per wait, per gap."""
    wait_codes = sorted({*(c for c in codebook.codes if micro_pause_s <= c < look_again_s), look_again_s})
    gaps = []
    for current, following in zip(actions, actions[1:]):
        end = current["t_start"] + current["executed_s"]
        # A recorded hold that outlasts what the label executes keeps its key down: the
        # remainder is not idle.
        idle_start = current["t_end"] if current["t_end"] - end > micro_pause_s else end
        left = following["t_start"] - idle_start
        waits, t = [], idle_start
        while left > micro_pause_s:
            duration = look_again_s if left >= look_again_s else codebook.snap(left, wait_codes)
            waits.append((t, duration))
            t += duration
            left -= duration
        gaps.append(waits)
    return gaps


WAIT_POLICIES: dict[str, Callable[..., list[list[tuple[float, float]]]]] = {
    "closed_loop": closed_loop_waits,
}


def build_entries(timeline: dict[str, Any], spec: dict[str, Any], allowed: Callable[[dict], bool],
                  fps: float) -> list[dict[str, Any]]:
    """The episode's decisions in time order. Entries whose frame falls outside the
    video are dropped (they cannot be shown). Actions the current controls forbid stay
    (the player did them) but can never be targets; so do logged actions the executor
    rejected (the model saw them in its history)."""
    params = dict(spec["codebook"])
    codebook = CODEBOOKS[params.pop("name")](**params)
    offset, video = timeline["video_offset_s"], timeline["video_duration_s"]
    last_frame = int(video * fps)

    entries = []

    def add(kind: str, t: float, label: dict, **extra: Any) -> None:
        video_t = t - offset
        if not 0.0 <= video_t <= video:
            return
        entries.append({"kind": kind, "t": round(t, 4), "frame": min(last_frame, max(0, round(video_t * fps))),
                        "label": label, **extra})

    if timeline["decisions"]:
        # Logged waits snap like parsed ones: the codes plus the wait policy's look-again time.
        look_again = spec["waits"].get("look_again_s")
        wait_codes = sorted({*codebook.codes, float(look_again)}) if look_again else None
        waits_in_run = 0
        for decision in timeline["decisions"]:
            if len(decision["actions"]) != 1:
                continue  # no action parsed (or a multi-action plan this format cannot train)
            label, executed = codebook.recorded(decision["actions"][0], timeline["hold_duration_s"], wait_codes)
            if label["action"] == "wait":
                add("wait", decision["t"], label, position=waits_in_run, source="recorded",
                    thought=decision["thought"], note=decision.get("note", ""))
                waits_in_run += 1
                continue
            waits_in_run = 0
            add("action", decision["t"], label, t_end=round(decision["t"] + executed, 4),
                target_ok=decision["valid"][0] and allowed(label), action_type=label["action"],
                source="recorded", thought=decision["thought"], note=decision.get("note", ""))
        for index, entry in enumerate(entries):
            entry["index"] = index
        return entries

    actions = []
    for action in timeline["actions"]:
        label, executed = codebook.label(action)
        actions.append({**action, "label": label, "executed_s": executed})
    wait_params = dict(spec["waits"])
    gaps = WAIT_POLICIES[wait_params.pop("name")](actions, codebook, **wait_params)
    for index, action in enumerate(actions):
        add("action", action["t_start"], action["label"], t_end=round(action["t_end"], 4),
            target_ok=allowed(action["label"]), action_type=action["label"]["action"])
        if index < len(gaps):
            for position, (t, duration) in enumerate(gaps[index]):
                add("wait", t, {"action": "wait", "duration": duration}, position=position)
    for index, entry in enumerate(entries):
        entry["index"] = index
    return entries
