"""Add short action notes in episode order, using the existing player interfaces."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from itertools import groupby, islice
import json
from pathlib import Path
import threading

from agents import create_client, create_client_config
from agents.types import ContextEntry
from catalog.models import load_model
from harnesses.vlm.prompting import ACTION_CONTRACT

from .prompts import memory_action, system_prompt


NOTE_INSTRUCTION = """The next action has already been selected and is given below.
Return that action with a short `note`, as if you were playing now. Use one short
sentence, or two when necessary. Explain the immediate visible reason or intent,
using the current screenshot and previous actions and notes. Do not describe data
annotation, a policy, or hidden game state. Do not claim a later outcome.
When the reason is uncertain, describe what is visible instead of inventing one.
For wait, explain a visible transition if there is one; otherwise describe the
current scene. Do not invent a cooldown or animation just to justify waiting.
Examples of the desired style, only when supported by the screen or history:
- The ball is heading left, so I move the paddle under its path.
- The player is beside the wall; I try the open passage above.
- The level transition is still on screen, so I wait for play to resume.
- The grid is unchanged, with the player beside the wall.
Use minimal thinking. The short note is the only explanation kept for training.
"""


def load_notes(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {row["id"]: row["note"] for row in map(json.loads, handle)}


def _step(row: dict) -> int:
    return int(row["id"].rsplit(":", 1)[1]) + 1


def _same_action(actual: dict, selected: dict) -> bool:
    kind = selected["action"]
    if actual.get("action") != kind:
        return False
    if kind == "press_key":
        return actual.get("key") == selected["key"]
    if kind in {"press_keys", "set_keys"}:
        return set(actual.get("keys", [])) == set(selected["keys"])
    if kind == "key_chunk":
        return ([set(s.get("keys", [])) for s in actual.get("steps", [])]
                == [set(s["keys"]) for s in selected["steps"]])
    return True


def _context(row: dict, known: dict[int, str], dataset: Path) -> list[ContextEntry]:
    history = row["history"]
    entries = []
    for turn in history:
        step = turn["step"]
        entries.append(ContextEntry("text", "user", text=str(step), field="step"))
        if turn.get("frame"):
            entries.append(ContextEntry("image", "user", image_path=str(dataset / turn["frame"])))
        entries.append(ContextEntry("text", "assistant", text=json.dumps(turn["action"]), field="action"))
        note = turn.get("note") or known.get(step)
        if note:
            entries.append(ContextEntry("text", "assistant", text=note, field="note"))
    return entries


def generate_notes(dataset: Path, model: str, *, thinking: str | None = None,
                   workers: int = 1, episodes: int | None = None, log=print) -> dict:
    """Keep recorded notes; append each new note immediately so reruns resume it.

    build writes contiguous episodes. Each worker handles one complete episode
    sequentially; only independent episodes run concurrently. The original rows,
    target actions and native thinking are never rewritten.
    """
    if workers < 1 or (episodes is not None and episodes < 1):
        raise ValueError("workers and episodes must be positive")
    dataset = dataset.resolve()
    manifest = json.loads((dataset / "manifest.json").read_text())
    profile = load_model(model)
    level = profile.resolve_thinking(thinking)
    profile = replace(profile, request_options={
        **profile.request_options, **profile.thinking_request(level),
    })
    path = dataset / "notes.jsonl"
    existing = load_notes(path)
    lock = threading.Lock()
    stopping = threading.Event()
    counts = Counter()

    def annotate(rows, destination):
        client = create_client(create_client_config(profile, action_contract=ACTION_CONTRACT))
        known = {}
        generated = kept = 0
        for row in sorted(rows, key=_step):
            if stopping.is_set():
                break
            note = str(row.get("note") or existing.get(row["id"]) or "").strip()
            if note:
                known[_step(row)] = note
                kept += 1
                continue
            game = manifest["games"][row["game"]]
            target = (memory_action(row["target"], game) if profile.codec == "qwen"
                      else json.dumps(row["target"], ensure_ascii=False))
            client.config.system_prompt = system_prompt(
                game, manifest["tasks"][f"{row['game']}/{row['task']}"],
                thinking=level != "off", codec=profile.codec,
            ) + "\n\n" + NOTE_INSTRUCTION + "\nSelected action:\n" + target
            actions = client.get_actions(dataset / row["frame"], _context(row, known, dataset))
            trace = client.pop_logged_interaction() or {}
            note = str(trace.get("note") or "").strip()
            if trace.get("error") or not note:
                raise ValueError(f"{row['id']}: note generation failed: {trace.get('error') or 'empty note'}")
            if len(actions) != 1 or not _same_action(actions[0], row["target"]):
                raise ValueError(f"{row['id']}: the note model returned a different action")
            known[_step(row)] = note
            with lock:
                destination.write(json.dumps({"id": row["id"], "note": note}, ensure_ascii=False) + "\n")
                destination.flush()
            generated += 1
        with lock:
            counts.update(episodes=1, generated=generated, kept=kept)
            log(f"notes: {rows[0]['episode']}: {generated} generated, {kept} already present")

    with (dataset / "rows.jsonl").open(encoding="utf-8") as source, path.open("a", encoding="utf-8") as destination:
        grouped = groupby(map(json.loads, source), key=lambda row: row["episode"])
        selected = grouped if episodes is None else islice(grouped, episodes)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = set()
            try:
                for _, rows in selected:
                    pending.add(pool.submit(annotate, list(rows), destination))
                    if len(pending) >= workers:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for future in done:
                            future.result()
                for future in pending:
                    future.result()
            except BaseException:
                stopping.set()
                for future in pending:
                    future.cancel()
                raise
    return {"output": str(path), **counts}
