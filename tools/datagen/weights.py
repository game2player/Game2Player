"""Training weights: how often each target is used per epoch.

Action targets get signal factors from the recorded game state (applied in spec
order), then a per-game budget spreads each game's budget over its targets in
proportion to those factors. Waits then get a weight from the action they lead to.
A weight is the expected number of uses of a row per epoch; nothing is copied.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any


class States:
    def __init__(self, states: dict[str, list[float]]):
        self.t = list(states["t"])
        self.progress = list(states["progress"])
        self.final_t = self.t[-1] if self.t else 0.0

    def at(self, t: float) -> float:
        if not self.t:
            return 0.0
        return self.progress[max(0, bisect.bisect_right(self.t, t) - 1)]

    def max_between(self, start: float, end: float) -> float:
        if not self.t:
            return 0.0
        left, right = bisect.bisect_left(self.t, start), bisect.bisect_right(self.t, end)
        return max(self.progress[left:right]) if left < right else self.at(start)

    def events(self, min_gap_s: float = 0.35, floor: float = 0.02) -> list[float]:
        """Sparse material progress events: a jump of at least the 75th-percentile
        positive step (and `floor`), or crossing a decile; at most one per `min_gap_s`."""
        if len(self.t) < 2:
            return []
        deltas = sorted(b - a for a, b in zip(self.progress, self.progress[1:]) if b - a > 1e-8)
        if not deltas:
            return []
        material = max(floor, deltas[min(len(deltas) - 1, int(0.75 * (len(deltas) - 1)))])
        events: list[float] = []
        previous = self.progress[0]
        previous_bin = math.floor(max(0.0, min(1.0, previous)) * 10 + 1e-8)
        for index in range(1, len(self.progress)):
            current = self.progress[index]
            current_bin = math.floor(max(0.0, min(1.0, current)) * 10 + 1e-8)
            if current - previous >= material - 1e-8 or current_bin > previous_bin:
                if not events or self.t[index] - events[-1] >= min_gap_s:
                    events.append(self.t[index])
            previous = current
            previous_bin = max(previous_bin, current_bin)
        return events


def _signature(label: dict[str, Any]) -> str:
    kind = label.get("action")
    compact: dict[str, Any] = {"action": kind}
    if kind == "press_key":
        compact["key"] = label.get("key")
    elif kind == "press_keys":
        compact["keys"] = sorted(label.get("keys") or [])
    elif kind == "key_chunk":
        compact["steps"] = [sorted(step.get("keys") or []) for step in label.get("steps") or []]
    elif kind in {"click", "click_hold", "drag", "mouse_move"}:
        for key in ("x", "y", "x1", "y1", "x2", "y2", "button"):
            if key in label:
                compact[key] = label[key] if key == "button" else round(float(label[key]) / 25) * 25
    elif kind == "scroll":
        compact["delta"] = [round(float(label.get(key) or 0)) for key in ("delta_x", "delta_y")]
    return json.dumps(compact, sort_keys=True)


# Each factor gets (actions, states, outcome, **params) and updates action["weight"] and
# action["tags"] in place. Actions are the episode's action entries in time order.

def progress_credit(actions, states, outcome, *, window_s, after_s, multipliers):
    for event_t in states.events():
        eligible = [a for a in actions if event_t - window_s <= a["t_end"] <= event_t + after_s]
        for rank, action in enumerate(reversed(eligible[-len(multipliers):])):
            action["weight"] = max(action["weight"], multipliers[rank])
            action["tags"].append(f"pre_progress_{rank + 1}")


def success_credit(actions, states, outcome, *, multipliers):
    if outcome == "success":
        for rank, action in enumerate(reversed(actions[-len(multipliers):])):
            action["weight"] = max(action["weight"], multipliers[rank])
            action["tags"].append(f"pre_success_{rank + 1}")


def _suffix(actions, states, *, max_actions, window_s, factor, tag):
    if not actions:
        return
    final_t = max(states.final_t, actions[-1]["t_end"])
    final_progress = states.max_between(0.0, final_t)
    for action in actions[-max_actions:]:
        if action["t_end"] >= final_t - window_s and final_progress <= states.at(action["t"]) + 1e-8:
            action["weight"] *= factor
            action["tags"].append(tag)


def failure_suffix(actions, states, outcome, *, max_actions, window_s, factor):
    if outcome == "fail":
        _suffix(actions, states, max_actions=max_actions, window_s=window_s, factor=factor,
                tag="fail_suffix")


def timeout_suffix(actions, states, outcome, *, max_actions, window_s, factor):
    if outcome == "timeout":
        _suffix(actions, states, max_actions=max_actions, window_s=window_s, factor=factor,
                tag="timeout_suffix")


def flat_repeat(actions, states, outcome, *, from_nth, window_s, factor):
    previous, run = None, 0
    for action in actions:
        signature = _signature(action["label"])
        run = run + 1 if signature == previous else 1
        previous = signature
        if run >= from_nth and action["weight"] > 0:
            if states.max_between(action["t_end"], action["t_end"] + window_s) <= states.at(action["t"]) + 1e-8:
                action["weight"] *= factor
                action["tags"].append("flat_repeat")


ACTION_FACTORS: dict[str, Callable[..., None]] = {
    "progress_credit": progress_credit, "success_credit": success_credit,
    "failure_suffix": failure_suffix, "timeout_suffix": timeout_suffix, "flat_repeat": flat_repeat,
}


def apply_action_factors(entries: list[dict], timeline: dict, factors: list[dict]) -> None:
    """Factors see only the actions that can be targets, in time order."""
    actions = []
    for entry in entries:
        if entry["kind"] == "action":
            entry["weight"], entry["tags"] = (1.0 if entry["target_ok"] else 0.0), []
            if entry["target_ok"]:
                actions.append(entry)
    states = States(timeline["states"])
    for factor in factors:
        params = dict(factor)
        ACTION_FACTORS[params.pop("name")](actions, states, timeline["outcome"], **params)


def _type_quotas(counts: Counter, budget: int) -> dict[str, int]:
    """Split a game's budget over action types in proportion to their counts, keeping
    at least one of each type when the budget allows, the rest to the largest pools."""
    total = sum(counts.values())
    if budget >= total:
        return dict(counts)
    quotas, assigned = {}, 0
    types = sorted(counts, key=lambda k: (-counts[k], k))
    for index, kind in enumerate(types):
        remaining_types = len(types) - index
        slots = budget - assigned
        natural = int(round(counts[kind] * budget / total))
        quota = min(counts[kind], max(natural, 1 if slots >= remaining_types else 0))
        quota = max(0, min(quota, slots - (remaining_types - 1)))
        quotas[kind] = quota
        assigned += quota
    leftover = budget - assigned
    for kind in sorted(types, key=lambda k: -(counts[k] - quotas[k])):
        if leftover <= 0:
            break
        take = min(leftover, counts[kind] - quotas[kind])
        quotas[kind] += take
        leftover -= take
    return quotas


def _spread(weights: list[float], quota: float, cap: float) -> list[float]:
    """Expected uses proportional to `weights`, summing to `quota`, none above `cap`
    (the excess goes to the others); zero weights stay zero."""
    out = [0.0] * len(weights)
    free = [i for i, w in enumerate(weights) if w > 0]
    left = float(quota)
    while free and left > 1e-12:
        total = sum(weights[i] for i in free)
        over = [i for i in free if left * weights[i] / total > cap]
        if not over:
            for i in free:
                out[i] = left * weights[i] / total
            break
        for i in over:
            out[i] = cap
            left -= cap
        capped = set(over)
        free = [i for i in free if i not in capped]
    return out


def game_temperature(episodes: list[list[dict]], *, alpha: float, stratify: str, max_copies: float) -> dict:
    """Each game's budget is median^(1-alpha) * n^alpha targets (n = its action
    targets, never more than n); within each action type the type's share of the
    budget is spread over targets in proportion to their factor weights."""
    by_game: dict[str, list[dict]] = defaultdict(list)
    for entries in episodes:
        for entry in entries:
            if entry["kind"] == "action" and entry["target_ok"]:
                by_game[entry["game_id"]].append(entry)
    counts = {game: len(rows) for game, rows in by_game.items()}
    median = int(statistics.median(counts.values())) if counts else 0
    report = {"median": median, "games": {}}
    for game, rows in by_game.items():
        n = counts[game]
        budget = n if n <= median else min(n, int(round(median ** (1 - alpha) * n ** alpha)))
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            groups[row[stratify] if stratify else "all"].append(row)
        quotas = _type_quotas(Counter({k: len(v) for k, v in groups.items()}), budget)
        unfilled = 0.0
        for kind, members in groups.items():
            spread = _spread([m["weight"] for m in members], quotas[kind], max_copies)
            for member, value in zip(members, spread):
                member["weight"] = value
            unfilled += quotas[kind] - sum(spread)
        report["games"][game] = {"targets": n, "budget": budget, "unfilled": round(unfilled, 3)}
    return report


BALANCERS: dict[str, Callable[..., dict]] = {"game_temperature": game_temperature}


def follow_next_action(entries: list[dict], *, decay: float, cap: float) -> None:
    """A wait is used as often as the action it leads to (at most `cap` times), times
    decay**k for the k-th wait of its run, so long runs teach "keep waiting" less."""
    following = None
    for entry in reversed(entries):
        if entry["kind"] == "action":
            following = entry
            entry.setdefault("tags", [])
        else:
            base = min(cap, following["weight"]) if following is not None and following["target_ok"] else 0.0
            entry["weight"] = base * decay ** entry["position"]
            entry["tags"] = []


WAIT_WEIGHTS: dict[str, Callable[..., None]] = {"follow_next_action": follow_next_action}


def stable_uniform(key: str) -> float:
    """A reproducible number in [0, 1) for a row key."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2 ** 64


def all_positive(entries: list[dict], *, episode_id: str) -> None:
    """Store every row that would ever be used."""
    for entry in entries:
        entry["selected"] = entry.get("weight", 0) > 0


def thin(entries: list[dict], *, episode_id: str, scale: float, seed: int) -> None:
    """Store a row with probability p = min(1, scale * weight) and divide its weight by p,
    so every row keeps the same expected uses per epoch; rows used often are always
    stored, rarely used ones only sometimes (fewer frames on disk)."""
    for entry in entries:
        weight = entry.get("weight", 0)
        if weight <= 0:
            entry["selected"] = False
            continue
        keep = min(1.0, scale * weight)
        entry["selected"] = stable_uniform(f"{seed}:{episode_id}:{entry['index']}") < keep
        if entry["selected"]:
            entry["weight"] = weight / keep


SELECTORS: dict[str, Callable[..., None]] = {"all_positive": all_positive, "thin": thin}
