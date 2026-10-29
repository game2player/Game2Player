"""Discard images and videos after a debugging stage has finished using them."""

from pathlib import Path


MEDIA_SUFFIXES = {".webm", ".mp4", ".mkv", ".avi", ".mov", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def remove_debug_media(directory: Path) -> int:
    """Keep code, results and event logs; return the number of bytes removed."""
    removed = 0
    for path in directory.rglob("*"):
        if path.suffix.lower() in MEDIA_SUFFIXES and path.is_file() and not path.is_symlink():
            removed += path.stat().st_size
            path.unlink(missing_ok=True)
    return removed
