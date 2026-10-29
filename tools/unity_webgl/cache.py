"""Project-local cache paths for Unity analysis artifacts."""

from __future__ import annotations

import os
from pathlib import Path

from tools.paths import cache_dir as project_cache_dir


def default_cache_dir() -> Path:
    configured = os.environ.get("GAMEENV_UNITY_CACHE")
    if configured:
        return Path(configured).expanduser().resolve()
    return project_cache_dir("unity_webgl")


def build_cache_dir(fingerprint: str, cache_dir: Path | None = None) -> Path:
    if len(fingerprint) != 64 or any(character not in "0123456789abcdef" for character in fingerprint):
        raise ValueError("Unity build fingerprint must be a lowercase SHA-256 digest")
    return (cache_dir or default_cache_dir()) / "builds" / fingerprint


__all__ = ["build_cache_dir", "default_cache_dir"]
