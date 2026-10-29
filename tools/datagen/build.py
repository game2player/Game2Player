"""Build a self-contained dataset directory from raw episodes and a spec.

    data/datasets/<name>/
      spec.yaml       the spec as written
      manifest.json   resolved spec, git commit, content snapshot
                      (game rules, controls, task prompts), episodes used, statistics
      rows.jsonl      one structured training target per line (no prompt text)
      frames/<episode>/<frame>.jpg   only the frames the rows show

Rows reference frames by relative path, so the directory can be moved or copied as is.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import tempfile
import copy
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tools.paths import cache_dir, temporary_dir

from .entries import CODEBOOKS, build_entries
from .frames import cut_frames, frame_name
from .prompts import allowed_by, snapshots
from .raw import RawEpisode, index_sources, read_json
from .spec import REPO, load_spec, parse_key
from .timeline import load_timeline
from .weights import BALANCERS, SELECTORS, WAIT_WEIGHTS, apply_action_factors

DATASETS = REPO / "data" / "datasets"
CACHE = cache_dir("datagen")
FRAME_BYTES_ESTIMATE = 100_000  # 1280x720 JPEGs at quality 3 average 55-90 KB
CAPTURE_WARN_SHARE = 0.05  # warn above this share of the injected inputs, and above
CAPTURE_WARN_MIN = 2       # this many (parsing can differ by one where timing is borderline)


def _git() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain", "--", "tools/datagen"))}


def _select_episodes(episodes: list[RawEpisode], spec: dict[str, Any]) -> tuple[list[RawEpisode], Counter, list[str]]:
    actors = set(spec["episodes"]["actors"])
    excluded: Counter = Counter()
    kept = []
    for episode in episodes:
        if episode.actor not in actors:
            excluded[f"actor_{episode.actor or 'unknown'}"] += 1
        elif spec["episodes"]["validity"] == "collector_strict" and episode.invalid:
            excluded[f"invalid_{episode.invalid}"] += 1
        else:
            kept.append(episode)
    unique = {}
    for episode in kept:
        key = (episode.source, episode.game_id, episode.task_id, episode.seed) if episode.seed is not None else episode.path
        if key in unique:
            excluded["duplicate_seed"] += 1
        unique[key] = episode
    kept = list(unique.values())
    unsolved_actors = set(spec["episodes"]["drop_unsolved"])
    solved = {(e.actor, e.game_id, e.task_id) for e in kept if e.outcome == "success"}
    unsolved = sorted({(e.game_id, e.task_id) for e in kept
                       if e.actor in unsolved_actors and (e.actor, e.game_id, e.task_id) not in solved})
    if unsolved:
        dropped = [e for e in kept if e.actor in unsolved_actors and (e.actor, e.game_id, e.task_id) not in solved]
        excluded["unsolved_task"] += len(dropped)
        kept = [e for e in kept if e.actor not in unsolved_actors or (e.actor, e.game_id, e.task_id) in solved]
    cap = spec["episodes"]["max_per_task"]
    if cap:
        by_task: dict[tuple[str, str], list[RawEpisode]] = defaultdict(list)
        for episode in kept:
            by_task[(episode.game_id, episode.task_id)].append(episode)
        rng = random.Random(spec["seed"])
        kept = []
        for key in sorted(by_task):
            group = sorted(by_task[key], key=lambda e: e.episode_id)
            chosen = group if len(group) <= cap else rng.sample(group, cap)
            excluded["over_max_per_task"] += len(group) - len(chosen)
            kept.extend(chosen)
    return sorted(kept, key=lambda e: (e.game_id, e.episode_id)), excluded, [f"{g}/{t}" for g, t in unsolved]


def _timeline_job(job: tuple[RawEpisode, dict, Path]) -> dict:
    episode, parse, cache = job
    return load_timeline(episode, parse, cache)


def _frames_job(job: tuple[Path, list[int], Path, int, int]) -> int:
    video, indices, out_dir, fps, quality = job
    return cut_frames(video, indices, out_dir, fps=fps, jpeg_quality=quality)


def _capture_by_game(episodes: list[RawEpisode], timelines: list[dict], log) -> dict[str, dict[str, int]]:
    """Sum each game's capture counts over its episodes that logged injected inputs, and
    warn where the page's own events were off."""
    by_game: dict[str, Counter] = defaultdict(Counter)
    for episode, timeline in zip(episodes, timelines):
        if timeline.get("capture"):
            by_game[episode.game_id].update(timeline["capture"])
    for game, counts in sorted(by_game.items()):
        for kind in ("press", "key", "wheel"):
            injected = counts[f"{kind}_injected"]
            unseen, extra = counts[f"{kind}_unseen"], counts[f"{kind}_page_only"]
            if max(unseen, extra) > max(CAPTURE_WARN_MIN, CAPTURE_WARN_SHARE * injected):
                log(f"capture: {game}: {kind}: {injected} injected, {unseen} not reported by the page, "
                    f"{extra} reported that nobody injected")
        for kind in ("keys", "chunk", "mouse"):
            injected, page = counts[f"parsed_{kind}_injected"], counts[f"parsed_{kind}_page"]
            if abs(injected - page) > max(CAPTURE_WARN_MIN, CAPTURE_WARN_SHARE * injected):
                log(f"capture: {game}: {page} {kind} actions parse from the page's events "
                    f"against {injected} from the injected inputs")
    return {game: dict(counts) for game, counts in sorted(by_game.items())}


def _route(row_id: str, entry: dict) -> dict[str, Any]:
    """A row's recorded-thinking route: a logged decision trains its
    thinking (or an empty think block when it had none); a parsed one only its action."""
    if entry.get("source") != "recorded":
        return {"id": row_id, "route": "masked"}
    if entry["thought"]:
        return {"id": row_id, "route": "think", "thought": entry["thought"]}
    return {"id": row_id, "route": "no_think"}


def _row(episode: RawEpisode, entries: list[dict], index: int, steps: int, images: int) -> dict:
    entry = entries[index]
    history = []
    for previous in range(max(0, index - steps), index):
        h = entries[previous]
        item = {"step": h["index"] + 1, "kind": h["kind"], "action": h["label"],
                "note": h.get("note", "")}
        if previous >= index - images:
            item["frame"] = f"frames/{episode.episode_id}/{frame_name(h['frame'])}"
        history.append(item)
    return {
        "id": f"{episode.episode_id}:{entry['index']}",
        "game": episode.game_id, "task": episode.task_id, "episode": episode.episode_id,
        "actor": episode.actor, "model": episode.model,
        "t": entry["t"], "kind": entry["kind"],
        "frame": f"frames/{episode.episode_id}/{frame_name(entry['frame'])}",
        "target": entry["label"], "thinking": entry.get("thought", ""),
        "note": entry.get("note", ""), "history": history,
        "weight": round(entry["weight"], 6), "tags": entry.get("tags", []),
    }


def build(spec_path: Path, *, root: Path = DATASETS, workers: int = 16, overwrite: bool = False,
          dry_run: bool = False, log=print) -> Path | dict:
    spec = load_spec(spec_path)
    out = Path(root) / spec["name"]
    if out.exists() and not overwrite and not dry_run:
        raise FileExistsError(f"{out} exists; pass --overwrite to rebuild it")
    source_paths = spec["sources"].values() if isinstance(spec["sources"], dict) else spec["sources"]
    sources = [(REPO / s).resolve() if not Path(s).is_absolute() else Path(s) for s in source_paths]
    wanted = set(spec["games"])
    fps, quality = int(spec["frames"]["fps"]), int(spec["frames"]["jpeg_quality"])

    episodes, excluded, unsolved = _select_episodes(index_sources(sources, wanted), spec)
    log(f"{len(episodes)} episodes from {len({e.game_id for e in episodes})} games; excluded {dict(excluded)}")
    if unsolved:
        log(f"left out {len(unsolved)} tasks no {'/'.join(spec['episodes']['drop_unsolved'])} episode solved: "
            f"{', '.join(unsolved)}")
    games, tasks = snapshots({e.episode_id: read_json(e.path / "summary.json") for e in episodes})

    cache = CACHE / "timelines" / parse_key(spec)
    with ProcessPoolExecutor(workers) as pool:
        timelines = list(pool.map(_timeline_job, [(e, spec["parse"], cache) for e in episodes], chunksize=8))
    log(f"timelines ready ({cache})")
    capture = _capture_by_game(episodes, timelines, log)

    per_episode = []
    for done, (episode, timeline) in enumerate(zip(episodes, timelines), start=1):
        entries = build_entries(timeline, spec, allowed_by(games[episode.game_id]), fps)
        for entry in entries:
            entry["game_id"] = episode.game_id
        apply_action_factors(entries, timeline, spec["action_weights"])
        per_episode.append(entries)
        if done % 2000 == 0:
            log(f"entries: {done}/{len(episodes)} episodes")
    balance_params = dict(spec["balance"])
    balance_report = BALANCERS[balance_params.pop("name")](per_episode, **balance_params)
    log("action weights balanced")
    wait_params = dict(spec["wait_weights"])
    wait_weight = WAIT_WEIGHTS[wait_params.pop("name")]
    for entries in per_episode:
        wait_weight(entries, **wait_params)
    select_params = dict(spec["selection"])
    select = SELECTORS[select_params.pop("name")]
    for episode, entries in zip(episodes, per_episode):
        select(entries, episode_id=episode.episode_id, **select_params)

    steps = int(spec["history"]["steps"])
    images = spec["history"]["images"]
    images = steps if images is None else int(images)
    needed = {}
    totals, routes, rows_per_episode = Counter(), Counter(), Counter()
    by_source: dict[str, Counter] = defaultdict(Counter)
    stats: dict[str, Counter] = defaultdict(Counter)
    for episode, entries in zip(episodes, per_episode):
        frames: set[int] = set()
        for index, entry in enumerate(entries):
            if not entry["selected"]:
                continue
            history = entries[max(0, index - images):index]
            frames.add(entry["frame"])
            frames.update(h["frame"] for h in history)
            row = _row(episode, entries, index, steps, images)
            totals["rows"] += 1
            totals["json_bytes"] += len(json.dumps(row, ensure_ascii=False).encode("utf-8")) + 1
            totals[f"{entry['kind']}_rows"] += 1
            totals[f"{entry['kind']}_weight"] += row["weight"]
            routes[_route(row["id"], entry)["route"]] += 1
            rows_per_episode[episode.episode_id] += 1
            source_counts = by_source[episode.source.name]
            source_counts["rows"] += 1
            source_counts["with_thinking"] += bool(row["thinking"])
            source_counts["with_note"] += bool(row["note"])
            stats[episode.game_id][f"{entry['kind']}_rows"] += 1
            stats[episode.game_id][f"{entry['kind']}_weight"] += entry["weight"]
        if frames:
            needed[episode.episode_id] = (episode, sorted(frames))

    total_frames = sum(len(frames) for _episode, frames in needed.values())
    out.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out.parent).free
    estimate = total_frames * FRAME_BYTES_ESTIMATE + totals["json_bytes"]
    log(f"{totals['rows']} rows, {total_frames} frames (~{estimate / 1e9:.1f} GB including rows); {free / 1e9:.0f} GB free")
    if dry_run:
        return {"episodes": len(episodes), "rows": totals["rows"], "frames": total_frames,
                "structured_json_bytes": totals["json_bytes"], "per_source": dict(by_source),
                "expected_uses_per_epoch": round(totals["action_weight"] + totals["wait_weight"], 1),
                "rows_by_kind": {k: totals[f"{k}_rows"] for k in ("action", "wait")},
                "uses_by_kind": {k: round(totals[f"{k}_weight"], 1)
                                 for k in ("action", "wait")},
                "per_game": {g: dict(c) for g, c in sorted(stats.items())}, "balance": balance_report,
                "excluded_episodes": dict(excluded),
                "unsolved_tasks_left_out": unsolved,
                "episodes_with_logged_decisions": sum(1 for t in timelines if t["decisions"]),
                "episodes_with_injected_inputs": sum(1 for t in timelines if t["input_source"] == "executed"),
                "capture": capture,
                "thinking_routes": dict(routes)}
    if estimate + 10e9 > free:
        raise RuntimeError("not enough disk for the frames plus a 10 GB margin")

    tmp = out.with_name(out.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "frames").mkdir(parents=True)
    jobs = [(e.path / "video.webm", idx, tmp / "frames" / e.episode_id, fps, quality) for e, idx in needed.values()]
    clamped = 0
    with ProcessPoolExecutor(workers) as pool:
        for done, count in enumerate(pool.map(_frames_job, jobs), start=1):
            clamped += count
            if done % 200 == 0:
                log(f"frames: {done}/{len(jobs)} episodes")
    with (tmp / "rows.jsonl").open("w", encoding="utf-8") as handle:
        for episode, entries in zip(episodes, per_episode):
            for index, entry in enumerate(entries):
                if entry["selected"]:
                    row = _row(episode, entries, index, steps, images)
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    codebook_params = dict(spec["codebook"])
    codebook = CODEBOOKS[codebook_params.pop("name")](**codebook_params)
    manifest = {
        "row_schema_version": 2,
        "name": spec["name"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git": _git(),
        "spec": spec,
        "parse_key": parse_key(spec),
        "codebook": codebook.describe(),
        "games": games,
        "tasks": tasks,
        "episodes": [{"id": e.episode_id, "game": e.game_id, "task": e.task_id, "outcome": e.outcome,
                      "actor": e.actor, "model": e.model, "decisions": "logged" if timeline["decisions"] else "parsed",
                      "inputs": timeline["input_source"],
                      "source": str(e.path.relative_to(REPO)) if e.path.is_relative_to(REPO) else str(e.path),
                      "rows": rows_per_episode.get(e.episode_id, 0)} for e, timeline in zip(episodes, timelines)],
        "excluded_episodes": dict(excluded),
        "unsolved_tasks_left_out": unsolved,
        "balance": balance_report,
        "stats": {
            "rows": totals["rows"], "per_source": dict(by_source),
            "frames": total_frames,
            "frames_past_video_end": clamped,
            "expected_uses_per_epoch": round(totals["action_weight"] + totals["wait_weight"], 3),
            "per_game": {g: {k: round(v, 3) if isinstance(v, float) else v for k, v in c.items()}
                         for g, c in sorted(stats.items())},
            "capture": capture,
        },
    }
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    shutil.copyfile(spec_path, tmp / "spec.yaml")
    if out.exists():
        shutil.rmtree(out)
    tmp.rename(out)
    log(f"dataset written: {out}")
    return out


def build_sources(spec_path: Path, *, root: Path = DATASETS, workers: int = 16,
                  overwrite: bool = False, dry_run: bool = False, log=print) -> dict:
    """Build one independent dataset per source directory; never merge their rows."""
    spec = load_spec(spec_path)
    destination = Path(root) / spec["name"]
    items = list(spec["sources"].items()) if isinstance(spec["sources"], dict) else [
        (Path(source).name, source) for source in spec["sources"]]
    names = [name for name, _ in items]
    if len(names) != len(set(names)):
        raise ValueError("source directory names must be distinct")
    results = {}
    with tempfile.TemporaryDirectory(dir=temporary_dir("datagen")) as temporary:
        for name, source in items:
            single = copy.deepcopy(spec)
            single.update(name=name, sources=[source])
            path = Path(temporary) / f"{name}.yaml"
            path.write_text(yaml.safe_dump(single, sort_keys=False))
            log(f"source: {source}")
            result = build(path, root=destination, workers=workers, overwrite=overwrite,
                           dry_run=dry_run, log=log)
            results[name] = result if dry_run else str(result)
    return results
