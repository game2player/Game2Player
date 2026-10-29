from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator
import zipfile


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    path: Path
    member: str | None = None

    @property
    def spec(self) -> str:
        return f"{self.path}:{self.member}" if self.member else str(self.path)

    @contextmanager
    def open(self) -> Iterator[BinaryIO]:
        if self.member is None:
            with self.path.open("rb") as stream:
                yield stream
        else:
            with zipfile.ZipFile(self.path) as archive, archive.open(self.member) as stream:
                yield stream


def iter_assets(root: Path) -> Iterator[Asset]:
    """Match the launcher's loose-file precedence; inspect ZIP members in place."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Not a game directory: {root}")
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    loose = {path.relative_to(root).as_posix() for path in paths
             if not (path.parent == root and path.name.startswith("_bundle_") and path.suffix == ".zip")}
    seen: set[str] = set()
    for path in paths:
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Asset symlink leaves the game directory: {path}")
        if path.parent == root and path.name.startswith("_bundle_") and path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename.replace("\\", "/").lstrip("/")
                    member = PurePosixPath(name)
                    if ".." in member.parts or not name:
                        raise ValueError(f"Unsafe ZIP member: {info.filename}")
                    name = member.as_posix()
                    if name in seen:
                        raise ValueError(f"Duplicate ZIP member: {name}")
                    seen.add(name)
                    if name not in loose:
                        yield Asset(name, info.file_size, path, info.filename)
        else:
            yield Asset(path.relative_to(root).as_posix(), path.stat().st_size, path)


def has_marker(root: Path, marker: str) -> bool:
    needle = marker.encode("utf-8")
    if not needle:
        raise ValueError("Marker must not be empty")
    for asset in iter_assets(root):
        if Path(asset.name).suffix not in {".js", ".html"}:
            continue
        with asset.open() as stream:
            tail = b""
            while chunk := stream.read(64 * 1024):
                block = tail + chunk
                if needle in block:
                    return True
                tail = block[-(len(needle) - 1):] if len(needle) > 1 else b""
    return False
