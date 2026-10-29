"""Launch ms-swift SFT with explicit GPUs and a portable dataset directory."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

import yaml


def command(config: dict, datasets: list[Path], output: Path, extra: list[str], executable: str) -> list[str]:
    args = [executable, "sft"]
    for key, value in config.items():
        if value is not None:
            values = value if isinstance(value, list) else [value]
            args.extend(["--" + key, *(str(v).lower() if isinstance(v, bool) else str(v) for v in values)])
    return args + ["--dataset", *(str(path) for path in datasets),
                   "--external_plugins", str(Path(__file__).with_name("local_datasets.py")),
                   "--output_dir", str(output), *extra]


def dataset_files(paths: list[Path]) -> list[Path]:
    datasets = []
    for path in paths:
        path = path.resolve()
        if path.is_dir():
            path = path / "train.jsonl"
        if not path.is_file():
            raise ValueError(f"dataset does not exist: {path}")
        if path in datasets:
            raise ValueError(f"dataset selected more than once: {path}")
        datasets.append(path)
    return datasets


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="+", type=Path, help="one or more dataset directories or train.jsonl files")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gpus", required=True, help="explicit GPU indices, e.g. 0 or 0,1,2")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("qwen35_full.yaml"))
    parser.add_argument("--swift", default="swift", help="ms-swift executable from the training environment")
    parser.add_argument("--master-port", type=int, default=29531)
    parser.add_argument("--dry-run", action="store_true")
    args, extra = parser.parse_known_args(argv)
    if extra and extra[0] == "--":
        extra = extra[1:]
    try:
        datasets = dataset_files(args.datasets)
    except ValueError as error:
        parser.error(str(error))
    devices = args.gpus.split(",")
    if not devices or any(not d.isdigit() for d in devices) or len(set(devices)) != len(devices):
        parser.error("--gpus must list distinct numeric GPU indices")
    executable = shutil.which(args.swift)
    if executable is None and not args.dry_run:
        parser.error("ms-swift is missing; activate the training Conda environment")
    executable = executable or args.swift
    config = yaml.safe_load(args.config.read_text())
    cmd = command(config, datasets, args.output.resolve(), extra, executable)
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=args.gpus, NPROC_PER_NODE=str(len(devices)),
               MASTER_PORT=str(args.master_port), GAME2PLAYER_TRAIN_DATASETS=json.dumps([str(path) for path in datasets]),
               IMAGE_MAX_TOKEN_NUM="1024", TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="4",
               PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
    details = {"command": cmd, "dataset_roots": [str(path.parent) for path in datasets], "gpus": devices}
    print(json.dumps(details, indent=2), flush=True)
    if args.dry_run:
        return 0
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
