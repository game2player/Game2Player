"""Make a portable local dataset package; this module never uploads anything."""
from __future__ import annotations

import gzip
import json
import shlex
from pathlib import Path
import shutil
import tarfile
from typing import Callable


def package_dataset(dataset: Path, output: Path, *, shard_bytes: int = 2 * 1024**3,
                    include_rows: bool = False,
                    include_training: bool = True,
                    log: Callable[[str], None] | None = None) -> dict:
    dataset, output = dataset.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Each image occurs once in the tar shards, even if many training rows use it.
    shards, images, image_bytes = 0, 0, 0
    archive = None
    current_bytes = 0
    pending = None

    def close_shard() -> None:
        archive.close()
        final = pending.with_suffix("")
        pending.rename(final)
        if log:
            log(f"Ready: {final.name} ({final.stat().st_size / 1024**3:.2f} GiB); {images:,} images packed")

    try:
        for episode in sorted((dataset / "frames").iterdir()):
            for frame in sorted(episode.glob("*.jpg")):
                size = frame.stat().st_size
                padded_size = 512 + ((size + 511) // 512) * 512
                if archive is None or current_bytes + padded_size > shard_bytes:
                    if archive is not None:
                        close_shard()
                    pending = output / f"frames-{shards:05d}.tar.partial"
                    archive = tarfile.open(pending, "w")
                    shards += 1
                    current_bytes = 0
                archive.add(frame, arcname=frame.relative_to(dataset).as_posix(), recursive=False)
                current_bytes += padded_size
                images += 1
                image_bytes += size
    except BaseException:
        if archive is not None:
            archive.close()
        raise
    else:
        if archive is not None:
            close_shard()
    if log:
        log("Compressing training examples and converting image paths")
    rows = 0
    with (dataset / "train.jsonl").open() as src, gzip.open(output / "train.jsonl.gz.partial", "wt", compresslevel=6) as dst:
        for line in src:
            row = json.loads(line)
            row["images"] = [(dataset / image).resolve().relative_to(dataset).as_posix()
                             for image in row["images"]]
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows += 1
            if log and rows % 100_000 == 0:
                log(f"Compressed {rows:,} training examples")
    (output / "train.jsonl.gz.partial").rename(output / "train.jsonl.gz")
    for name in ("manifest.json", "spec.yaml", "render_stats.json", "verification.json"):
        if (dataset / name).is_file():
            shutil.copyfile(dataset / name, output / name)
    if include_rows:
        with (dataset / "rows.jsonl").open("rb") as src, gzip.open(output / "rows.jsonl.gz", "wb") as dst:
            shutil.copyfileobj(src, dst)
        if (dataset / "notes.jsonl").is_file():
            shutil.copyfile(dataset / "notes.jsonl", output / "notes.jsonl")
    if include_training:
        copy_training_files(output)
    result = {"training_rows": rows, "images": images, "image_bytes": image_bytes,
              "shards": shards, "payload_bytes": sum(p.stat().st_size for p in output.rglob("*") if p.is_file())}
    (output / "README.md").write_text(dataset_card(result, scripts_path="scripts" if include_training else "../../scripts"))
    # Written last so a background uploader can distinguish a complete package.
    (output / "package.json.partial").write_text(json.dumps(result, indent=2) + "\n")
    (output / "package.json.partial").rename(output / "package.json")
    if log:
        log(f"Package complete: {rows:,} examples, {images:,} images, {shards} shards")
    return result


def copy_training_files(output: Path) -> None:
    training = output / "scripts"
    training.mkdir()
    source = Path(__file__).parents[1] / "train"
    for original, target in (("__main__.py", "train.py"), ("local_datasets.py", "local_datasets.py"),
                             ("environment.yaml", "environment.yaml"), ("qwen35_lora.yaml", "qwen35_lora.yaml")):
        shutil.copyfile(source / original, training / target)


def package_collection(dataset: Path, output: Path, **kwargs) -> dict:
    """Keep source directories separate, with one shared training launcher."""
    dataset, output = dataset.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    copy_training_files(output)
    sources = sorted(path for path in dataset.iterdir() if (path / "train.jsonl").is_file())
    if not sources:
        raise ValueError(f"no dataset directories containing train.jsonl under {dataset}")
    reports = {path.name: package_dataset(path, output / "data" / path.name, include_training=False, **kwargs)
               for path in sources}
    (output / "README.md").write_text(collection_card(reports))
    result = {"datasets": reports}
    return result


def collection_card(reports: dict) -> str:
    names = list(reports)
    first = shlex.quote(f"./data/{names[0]}")
    examples = " ".join(shlex.quote(f"./data/{name}") for name in names[:2])
    table = "\n".join(f"| `data/{name}/` | {report['training_rows']:,} | {report['images']:,} |"
                      for name, report in reports.items())
    total = sum(report['training_rows'] for report in reports.values())
    return f'''---
language:
- en
tags:
- multimodal
- games
- supervised-fine-tuning
pretty_name: Game trajectory SFT data
---

# Train with this dataset

This package contains **{total:,} training examples**.

| Dataset directory | Examples | Images |
| --- | ---: | ---: |
{table}

## Download

Replace `YOUR_ORG/YOUR_DATASET` with the Hugging Face repository hosting this package.

```bash
python -m pip install 'huggingface_hub>=1.0,<2'
hf download YOUR_ORG/YOUR_DATASET --repo-type dataset --local-dir ./dataset-export
cd dataset-export
```

To download one source, add `--include "scripts/*" "data/SOURCE/*"` to the download
command. Replace `SOURCE` with a directory name from the table.

## Extract

Run from `dataset-export/`:

```bash
for directory in data/*/; do
  for shard in "$directory"frames-*.tar; do tar -xf "$shard" -C "$directory"; done
  gzip -dk "${{directory}}train.jsonl.gz"
done
```

## Install training dependencies

```bash
conda env create -f scripts/environment.yaml
conda activate game2player-swift
```

The environment uses CUDA 12.8 PyTorch wheels. Adjust the environment file before
installation if your hardware needs another build.

## Train

Select one dataset directory or pass several:

```bash
python scripts/train.py {first} --output ./output/lora --gpus 0
python scripts/train.py {examples} --output ./output/lora --gpus 0,1
```

Set `--gpus` to your GPU IDs. Add `--dry-run` to print the command without training.
Edit `scripts/qwen35_lora.yaml` to change the default Qwen3.5-4B LoRA settings, or
pass ms-swift options after `--`, for example `--model Qwen/Qwen3.5-9B`.
The first run downloads the model if it is not already cached.

Each `train.jsonl` row contains `messages` and image paths relative to its dataset
directory. Keep any `loss: false` fields when editing samples. Train on the
exported rows directly; do not apply preprocessing weights again.

Game imagery remains subject to the original games' licenses.
'''


def dataset_card(result: dict, *, scripts_path: str = "scripts") -> str:
    return f'''# Use this dataset

This directory contains **{result["training_rows"]:,} training examples** and
**{result["images"]:,} images** in {result["shards"]} image shards.

Run these commands from this directory.
If `train.jsonl` and `frames/` are already present, continue with installation.

## Extract

```bash
for shard in frames-*.tar; do tar -xf "$shard"; done
gzip -dk train.jsonl.gz
```

After extraction, `train.jsonl` contains the samples and `frames/` contains the images.

## Install training dependencies

```bash
conda env create -f {scripts_path}/environment.yaml
conda activate game2player-swift
```

## Train

```bash
python {scripts_path}/train.py . --output ./output/lora --gpus 0
```

Set `--gpus` to your GPU IDs. Add `--dry-run` to inspect the command, or pass
additional ms-swift options after `--`. The default recipe is
`{scripts_path}/qwen35_lora.yaml`.

Keep any `loss: false` fields when editing samples. Train on the exported rows
directly; do not apply preprocessing weights again.
'''
