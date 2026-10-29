# Export training data

Run these from the repository root once trajectories are collected.

## Build a dataset

Write a spec such as `dataset.yaml` with your own source paths and game IDs. Each
name under `sources` becomes an output folder.

```yaml
name: my_dataset
sources:
  policy: data/trajs/policy
  vlm: data/trajs/vlm
episodes:
  actors: [policy, vlm]
games: [1_my-game]
history:
  steps: 50
  images: 6
```

```bash
python -m tools.datagen build dataset.yaml --workers 4
```

This writes `data/datasets/my_dataset/policy/` and `data/datasets/my_dataset/vlm/`,
each with `rows.jsonl`, `frames/`, `manifest.json` and `spec.yaml`. `--root` changes
the parent folder and `--overwrite` rebuilds an existing dataset.

`history` sets how many previous actions and screenshots each row keeps, 50 and 6 by
default, plus the current screenshot. List only the games you train on in `games`,
so evaluation games stay unseen.

Rows are weighted toward actions near progress or success, and away from stalled
repetition, failed endings and long waits. Each game contributes about equally.
Policy tasks that were never solved, and rows of zero weight, are dropped.
`action_weights`, `balance`, `wait_weights` and `episodes.drop_unsolved` change this.

## Add missing notes

A player model can write the notes that recorded steps are missing:

```bash
python -m tools.datagen notes data/datasets/my_dataset/policy \
  --model claude-player --workers 2
```

Notes are saved to `notes.jsonl`, and a rerun resumes. `--workers` sets how many
episodes run at once, `--episodes 10` stops after the first ten, and `--thinking`
picks the model's thinking level.

## Export SFT samples

For each source folder:

```bash
python -m tools.datagen render data/datasets/my_dataset/policy \
  data/datasets/my_dataset/policy/train.jsonl --reasoning recorded
python -m tools.datagen render data/datasets/my_dataset/vlm \
  data/datasets/my_dataset/vlm/train.jsonl --reasoning recorded
```

`--reasoning recorded` keeps the recorded native thinking. A code policy has none, so
its empty thinking block is marked `loss: false` and is not trained on. Without the
flag, samples carry no thinking. Notes are included either way.

`--history 20 --images 3` uses less history than the dataset stores. A row's weight
sets how many times it is written. `--weights none` writes each row once.

## Package for transfer

```bash
python -m tools.datagen package data/datasets/my_dataset /path/to/export
```

The package holds `data/policy/`, `data/vlm/` and `scripts/`, with each source as a
compressed `train.jsonl.gz` and image TAR shards. `--include-rows` adds `rows.jsonl`
and generated notes. The package's README explains how to unpack it and train.
