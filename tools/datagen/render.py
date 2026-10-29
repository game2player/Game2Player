"""Turn a dataset into a trainer file, optionally keeping recorded thinking.

Rendering happens when a training run starts and writes into that run's directory;
the dataset itself is never changed. Weights are applied by deterministic
resampling: a row with weight w appears floor(w) times plus once more with
probability w - floor(w), drawn from the seed and the row id alone. A fraction keeps
that share of each task's episodes (at least one), chosen by seed; for one seed a
smaller fraction's rendered rows are a subset of a larger one's, so data-scaling runs
are nested. Rows are shuffled in bounded batches, leaving full epoch shuffling to
the trainer. The recorded variant keeps native thinking and short action notes
separate; only short notes enter the text history.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .prompts import EMPTY_THINK, memory_action, system_prompt, tool_call, user_content
from .weights import stable_uniform


def _swift(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """ms-swift multimodal rows: `<image>` placeholders in order plus an images list."""
    out, images = [], []
    for message in messages:
        if isinstance(message["content"], str):
            out.append(message)
            continue
        text = ""
        for part in message["content"]:
            if part.get("type") == "image":
                images.append(part["image"])
                text += "<image>"
            else:
                text += part.get("text") or ""
        out.append({**message, "content": text})
    return {"messages": out, "images": images}


def episodes_kept(manifest: dict[str, Any], fraction: float, seed: int) -> set[str]:
    """`fraction` of each task's episodes that have rows (at least one), by seed."""
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    by_task: dict[tuple[str, str], list[str]] = defaultdict(list)
    for episode in manifest["episodes"]:
        if episode["rows"]:
            by_task[(episode["game"], episode["task"])].append(episode["id"])
    kept: set[str] = set()
    for ids in by_task.values():
        ordered = sorted(ids, key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).digest())
        kept.update(ordered[:max(1, round(fraction * len(ids)))])
    return kept


def render_swift(dataset: Path, output: Path, *, history: int | None = None, reasoning: str | None = None,
                 images: int | None = None, weights: str = "resample", seed: int = 0,
                 fraction: float = 1.0, shuffle_buffer: int = 10000) -> dict[str, Any]:
    dataset = dataset.resolve()
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    kept = episodes_kept(manifest, fraction, seed) if fraction < 1 else None
    built = int(manifest["spec"]["history"]["steps"])
    steps = built if history is None else int(history)
    if not 0 <= steps <= built:
        raise ValueError(f"history must be between 0 and the {built} steps the dataset stores")
    built_images = manifest["spec"]["history"].get("images")
    built_images = built if built_images is None else int(built_images)
    image_count = min(steps, built_images) if images is None else images
    if not 0 <= image_count <= min(steps, built_images):
        raise ValueError("images exceeds the available history images")
    if reasoning not in (None, "recorded"):
        raise ValueError("reasoning must be 'recorded' or omitted")
    inline = reasoning == "recorded"
    from .notes import load_notes
    notes = load_notes(dataset / "notes.jsonl")
    with_reasoning = inline
    prompts: dict[str, str] = {}
    rng = random.Random(seed)
    lines: list[str] = []
    counts: Counter = Counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    # Shuffle bounded batches so a million 50-step prompts need not fit in RAM.
    def flush(destination):
        rng.shuffle(lines)
        destination.writelines(line + "\n" for line in lines)
        lines.clear()

    if shuffle_buffer < 1:
        raise ValueError("shuffle_buffer must be positive")
    with tmp.open("w", encoding="utf-8") as destination, (dataset / "rows.jsonl").open(encoding="utf-8") as handle:
        for row in map(json.loads, handle):
            if kept is not None and row["episode"] not in kept:
                continue
            game = manifest["games"][row["game"]]
            task_key = f"{row['game']}/{row['task']}"
            note = str(row.get("note") or notes.get(row["id"]) or "").strip()
            if task_key not in prompts:
                prompts[task_key] = system_prompt(game, manifest["tasks"][task_key], thinking=with_reasoning)
            history_rows = row["history"][len(row["history"]) - steps:] if steps else []
            turns = []
            for index, h in enumerate(history_rows):
                step = h["step"]
                turn = {"step": step, "action": memory_action(h["action"], game),
                        "note": h.get("note") or notes.get(f"{row['episode']}:{step - 1}", "")}
                if index >= len(history_rows) - image_count and h.get("frame"):
                    turn["image"] = {"type": "image", "image": str(dataset / h["frame"])}
                turns.append(turn)
            user = user_content(str(dataset / row["frame"]), turns)
            answer = tool_call(row["target"], game, note=note)
            route = "no_think"
            if inline:
                item = {"thought": row.get("thinking", "")}
                route = "think" if item["thought"] else ("masked" if row.get("actor") == "policy" else "no_think")
            if route == "think":
                assistant = [{"role": "assistant", "content": f"<think>\n{item['thought'].strip()}\n</think>\n\n{answer}"}]
            elif route == "masked":
                assistant = [{"role": "assistant", "content": EMPTY_THINK, "loss": False},
                             {"role": "assistant", "content": answer}]
            elif route == "no_think":
                assistant = [{"role": "assistant", "content": EMPTY_THINK + answer}]
            else:
                raise ValueError(f"unknown route {route!r} for row {row['id']}")
            line = json.dumps(_swift([{"role": "system", "content": prompts[task_key]},
                                      {"role": "user", "content": user}, *assistant]), ensure_ascii=False)
            if weights == "resample":
                w = float(row["weight"])
                copies = int(math.floor(w)) + (1 if stable_uniform(f"{seed}:{row['id']}") < w - math.floor(w) else 0)
            elif weights == "none":
                copies = 1
            else:
                raise ValueError(f"unknown weights mode {weights!r}")
            lines.extend([line] * copies)
            if len(lines) >= shuffle_buffer:
                flush(destination)
            counts["rows"] += 1
            counts["written"] += copies
            counts[f"{row['kind']}_written"] += copies
            counts[f"{route}_written"] += copies
        flush(destination)
    tmp.replace(output)
    return {"dataset": str(dataset), "history": steps, "images": image_count, "reasoning": reasoning, "weights": weights,
            "shuffle_buffer": shuffle_buffer,
            "seed": seed, "fraction": fraction,
            "episodes": len(kept) if kept is not None else sum(1 for e in manifest["episodes"] if e["rows"]),
            **counts}

