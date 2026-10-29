"""Resolve each dataset's image paths relative to its own JSONL file."""
from __future__ import annotations

import json
import os
from pathlib import Path


def resolve_images(row: dict, root: Path) -> dict:
    images = []
    for image in row.get("images", []):
        if isinstance(image, str):
            image = str(root / image)
        elif isinstance(image, dict) and image.get("path"):
            image = {**image, "path": str(root / image["path"])}
        images.append(image)
    return {**row, "images": images}


def register_local_datasets(paths: list[str]) -> None:
    from swift.dataset import DatasetMeta, RowPreprocessor, register_dataset

    class LocalImages(RowPreprocessor):
        def __init__(self, root):
            super().__init__()
            self.root = root

        def preprocess(self, row):
            return resolve_images(row, self.root)

    for filename in paths:
        path = Path(filename).resolve()
        register_dataset(DatasetMeta(dataset_path=str(path), preprocess_func=LocalImages(path.parent)))


if os.environ.get("GAME2PLAYER_TRAIN_DATASETS"):
    register_local_datasets(json.loads(os.environ["GAME2PLAYER_TRAIN_DATASETS"]))
