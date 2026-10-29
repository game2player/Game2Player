"""Dataset specs: every choice a build makes, with the current defaults.

A spec YAML only lists what differs from the defaults below; unknown keys are errors,
so a typo cannot silently fall back to a default. Each pluggable step names its
implementation (`name`) and passes the rest as parameters.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from harnesses.vlm.memory import DEFAULT_HISTORY_IMAGES, DEFAULT_HISTORY_STEPS

REPO = Path(__file__).resolve().parents[2]

# Durations fitted on the raw hold times of the policy episodes: 1-D k-means on log
# duration plus codes wherever a hold was still >15% off, two significant digits.
LOG26_CODES = [0.05, 0.075, 0.088, 0.1, 0.12, 0.16, 0.2, 0.25, 0.3, 0.36, 0.42, 0.49,
               0.57, 0.66, 0.78, 0.94, 1.1, 1.5, 2.0, 2.3, 2.7, 3.2, 3.7, 4.9, 6.1, 7.8]

DEFAULTS: dict[str, Any] = {
    "name": None,
    "seed": 3407,
    # Raw collection roots (read only); episodes are <root>/<game_id>/<episode_id>/.
    "sources": [],
    "episodes": {
        "actors": ["policy"],
        "validity": "collector_strict",
        "max_per_task": None,
        # For these actors, a task none of whose episodes (from that actor) succeeded is
        # left out: a scripted policy that never solves it plays it wrong.
        "drop_unsolved": ["policy"],
    },
    # The games rows come from. Keeping evaluation games out is the spec author's job.
    "games": [],
    # How raw inputs become actions (cached per episode under .cache/datagen/timelines).
    "parse": {
        "click_move_tolerance_px": 8.0,
        "long_hold_split_s": 7.8,
        "micro_chunk": {"gap_s": 0.2, "max_steps": 5, "max_total_s": 0.8, "max_segment_s": 0.25},
    },
    # Labels: durations snap to the codes; presses shorter than tap_below_s are taps
    # (no duration). Episodes that recorded the model's decisions use those decisions;
    # the others are parsed from their key and mouse events.
    "codebook": {"name": "codes", "codes": LOG26_CODES, "tap_below_s": 0.075},
    "waits": {"name": "closed_loop", "look_again_s": 1.0, "micro_pause_s": 0.2},
    "history": {"steps": DEFAULT_HISTORY_STEPS, "images": DEFAULT_HISTORY_IMAGES},
    # Multiplicative/maximum factors on action targets, applied in order.
    "action_weights": [
        {"name": "progress_credit", "window_s": 5.0, "after_s": 0.15, "multipliers": [3.0, 2.0, 1.5]},
        {"name": "success_credit", "multipliers": [3.0, 2.0, 1.5]},
        {"name": "failure_suffix", "max_actions": 3, "window_s": 5.0, "factor": 0.0},
        {"name": "timeout_suffix", "max_actions": 5, "window_s": 10.0, "factor": 0.25},
        {"name": "flat_repeat", "from_nth": 3, "window_s": 5.0, "factor": 0.35},
    ],
    # Per-game budget of action targets; weights become expected uses per epoch.
    "balance": {"name": "game_temperature", "alpha": 0.5, "stratify": "action_type", "max_copies": 3.0},
    "wait_weights": {"name": "follow_next_action", "decay": 0.7, "cap": 1.0},
    # Which rows are stored (with their frames); a selector may rescale weights so the
    # expected uses per epoch stay the same.
    "selection": {"name": "all_positive"},
    "frames": {"fps": 60, "jpeg_quality": 3},
}


PLUGIN_SECTIONS = {"codebook", "waits", "balance", "wait_weights", "selection"}


def _merge(base: Any, override: Any, path: str) -> Any:
    if (path in PLUGIN_SECTIONS and isinstance(override, dict)
            and override.get("name", base["name"]) != base["name"]):
        # Another implementation of a pluggable step: its own parameters replace the
        # default's (the implementation rejects any it does not take).
        return copy.deepcopy(override)
    if isinstance(base, dict) and isinstance(override, dict) and base:
        unknown = set(override) - set(base)
        if unknown:
            raise ValueError(f"unknown spec keys under {path or '<root>'}: {sorted(unknown)}")
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _merge(base[key], value, f"{path}.{key}" if path else key)
        return merged
    return copy.deepcopy(override)


def load_spec(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    spec = _merge(DEFAULTS, raw, "")
    if not spec["name"]:
        raise ValueError("spec needs a name")
    if not spec["sources"]:
        raise ValueError("spec needs at least one source root")
    if not spec["games"]:
        raise ValueError("spec needs games")
    if not isinstance(spec["episodes"]["drop_unsolved"], list):
        raise ValueError("episodes.drop_unsolved lists actors")
    if spec["history"]["steps"] < 0:
        raise ValueError("history.steps must be >= 0")
    images = spec["history"]["images"]
    if images is not None and not 0 <= images <= spec["history"]["steps"]:
        raise ValueError("history.images must be between 0 and history.steps")
    return spec


def parse_key(spec: dict[str, Any]) -> str:
    """Cache key of the parse step: timelines depend only on the parse parameters."""
    blob = json.dumps({"parse": spec["parse"], "version": PARSE_VERSION}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


PARSE_VERSION = 7
