"""Project-owned caches and temporary working directories."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def cache_dir(*parts: str) -> Path:
    return REPO_ROOT.joinpath(".cache", *parts)


def temporary_dir(category: str) -> Path:
    path = cache_dir("tmp", category)
    path.mkdir(parents=True, exist_ok=True)
    return path
