"""Training data from raw episodes.

    python -m tools.datagen build SPEC.yaml [--root DIR] [--workers N] [--overwrite] [--dry-run]
        -> data/datasets/<name>/<source-directory>/ (separate data for every source)
    python -m tools.datagen notes DATASET --model PROFILE [--thinking LEVEL] [--workers N] [--episodes N]
        -> notes.jsonl (resumable short notes, leaving rows.jsonl unchanged)
    python -m tools.datagen render DATASET OUT.jsonl [--history H] [--images N] [--reasoning recorded]
                                   [--weights resample|none] [--seed S] [--fraction F]
        -> an ms-swift file for one training run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .build import build_sources
from .render import render_swift


def _fraction(text: str) -> float:
    value = float(text)
    if not 0 < value <= 1:
        raise argparse.ArgumentTypeError("--fraction is a share in (0, 1]")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.datagen", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("spec", type=Path)
    b.add_argument("--root", type=Path, help="where datasets go (default data/datasets)")
    b.add_argument("--workers", type=int, default=16)
    b.add_argument("--overwrite", action="store_true")
    b.add_argument("--dry-run", action="store_true", help="count rows and frames, write nothing but the timeline cache")
    r = sub.add_parser("render")
    r.add_argument("dataset", type=Path)
    r.add_argument("output", type=Path)
    r.add_argument("--history", type=int)
    r.add_argument("--images", type=int, help="number of previous screenshots, excluding the current one")
    r.add_argument("--reasoning", choices=("recorded",))
    r.add_argument("--weights", choices=("resample", "none"), default="resample")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--fraction", type=_fraction, default=1.0,
                   help="keep this share of each task's episodes (nested across fractions for one seed)")
    n = sub.add_parser("notes", help="fill missing notes sequentially within each episode")
    n.add_argument("dataset", type=Path)
    n.add_argument("--model", required=True, help="player profile id or YAML path")
    n.add_argument("--thinking", help="mode from the player profile; defaults to its first mode")
    n.add_argument("--workers", type=int, default=1, help="independent episodes at once")
    n.add_argument("--episodes", type=int, help="process only this many episodes")
    p = sub.add_parser("package", help="make local portable shards; does not upload")
    p.add_argument("dataset", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--shard-gb", type=float, default=2.0)
    p.add_argument("--include-rows", action="store_true")
    args = parser.parse_args()
    if args.command == "build":
        result = build_sources(args.spec, **({"root": args.root} if args.root else {}), workers=args.workers,
                       overwrite=args.overwrite, dry_run=args.dry_run,
                       log=lambda message: print(message, flush=True))
        if args.dry_run:
            print(json.dumps(result, indent=1))
    elif args.command == "render":
        print(json.dumps(render_swift(args.dataset, args.output, history=args.history,
                                      images=args.images,
                                      reasoning=args.reasoning, weights=args.weights, seed=args.seed,
                                      fraction=args.fraction)))
    elif args.command == "notes":
        from .notes import generate_notes
        print(json.dumps(generate_notes(args.dataset, args.model, thinking=args.thinking,
                                        workers=args.workers, episodes=args.episodes)))
    else:
        from .package import package_dataset, package_collection
        if args.shard_gb <= 0:
            parser.error("--shard-gb must be positive")
        package = package_dataset if (args.dataset / "train.jsonl").is_file() else package_collection
        print(json.dumps(package(args.dataset, args.output,
                                        shard_bytes=int(args.shard_gb * 1024**3),
                                        include_rows=args.include_rows,
                                        log=lambda message: print(message, flush=True))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
