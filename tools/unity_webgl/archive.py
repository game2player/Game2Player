"""Bounded decompression and UnityWebData archive access."""

from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


UNITY_DATA_MAGIC = b"UnityWebData1.0\0"
WASM_MAGIC = b"\0asm"
_COPY_CHUNK = 1024 * 1024
_MAX_HEADER_SIZE = 64 * 1024 * 1024
_MAX_MEMBER_COUNT = 100_000


class ArchiveError(RuntimeError):
    """Raised for malformed, unsupported, or unsafe Unity payloads."""


@dataclass(frozen=True, slots=True)
class UnityDataMember:
    path: str
    offset: int
    size: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_COPY_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_encoding(path: Path, *, expected_magic: bytes | None = None) -> str:
    """Classify by content first; captured files often retain stale suffixes."""
    with path.open("rb") as handle:
        prefix = handle.read(max(16, len(expected_magic or b"")))
    if expected_magic and prefix.startswith(expected_magic):
        return "identity"
    if prefix.startswith((UNITY_DATA_MAGIC, WASM_MAGIC)):
        return "identity"
    if prefix.startswith(b"\x1f\x8b"):
        return "gzip"
    if path.name.lower().endswith((".br", ".unityweb")):
        return "brotli"
    return "identity"


def _atomic_target(cache_dir: Path, source: Path, encoding: str) -> tuple[Path, Path]:
    key = sha256_file(source)
    suffix = ".wasm" if "wasm" in source.name.lower() else ".data"
    target = cache_dir / "decoded" / f"{key}{suffix}"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target, temporary


def materialize_payload(
    source: Path,
    cache_dir: Path,
    *,
    expected_magic: bytes | None = None,
) -> Path:
    """Return a seekable decoded payload, reusing an external content cache."""
    source = source.resolve()
    encoding = payload_encoding(source, expected_magic=expected_magic)
    if encoding == "identity":
        if expected_magic:
            with source.open("rb") as handle:
                if handle.read(len(expected_magic)) != expected_magic:
                    raise ArchiveError(f"Unexpected payload header in {source}")
        return source

    target, temporary = _atomic_target(cache_dir, source, encoding)
    if target.is_file():
        with target.open("rb") as handle:
            cached_prefix = handle.read(len(expected_magic or b""))
        if not expected_magic or cached_prefix == expected_magic:
            return target
        target.unlink()

    try:
        if encoding == "gzip":
            with gzip.open(source, "rb") as reader, temporary.open("wb") as writer:
                shutil.copyfileobj(reader, writer, length=_COPY_CHUNK)
        elif encoding == "brotli":
            try:
                import brotli  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ArchiveError(
                    f"Brotli support is needed to decode {source}; install the optional 'brotli' package"
                ) from exc
            decompressor = brotli.Decompressor()
            with source.open("rb") as reader, temporary.open("wb") as writer:
                for chunk in iter(lambda: reader.read(_COPY_CHUNK), b""):
                    decoded = decompressor.process(chunk)
                    if decoded:
                        writer.write(decoded)
                if hasattr(decompressor, "is_finished") and not decompressor.is_finished():
                    raise ArchiveError(f"Truncated Brotli payload: {source}")
        else:  # pragma: no cover - guarded above
            raise ArchiveError(f"Unsupported payload encoding: {encoding}")

        if expected_magic:
            with temporary.open("rb") as handle:
                if handle.read(len(expected_magic)) != expected_magic:
                    raise ArchiveError(f"Decoded payload has an unexpected header: {source}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


class UnityDataArchive:
    """Read the simple container used by Unity WebGL ``.data`` payloads."""

    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()
        self._members = self._read_index()

    def _read_index(self) -> tuple[UnityDataMember, ...]:
        file_size = self.path.stat().st_size
        with self.path.open("rb") as handle:
            if handle.read(len(UNITY_DATA_MAGIC)) != UNITY_DATA_MAGIC:
                raise ArchiveError(f"Not a UnityWebData archive: {self.path}")
            raw_header_size = handle.read(4)
            if len(raw_header_size) != 4:
                raise ArchiveError(f"Truncated UnityWebData header: {self.path}")
            header_size = struct.unpack("<I", raw_header_size)[0]
            if not 20 <= header_size <= min(file_size, _MAX_HEADER_SIZE):
                raise ArchiveError(f"Invalid UnityWebData header size {header_size}: {self.path}")

            members: list[UnityDataMember] = []
            while handle.tell() < header_size:
                if len(members) >= _MAX_MEMBER_COUNT:
                    raise ArchiveError(f"Too many UnityWebData members: {self.path}")
                raw = handle.read(12)
                if len(raw) != 12:
                    raise ArchiveError(f"Truncated UnityWebData member header: {self.path}")
                offset, size, path_size = struct.unpack("<III", raw)
                if path_size <= 0 or handle.tell() + path_size > header_size:
                    raise ArchiveError(f"Invalid UnityWebData member name length: {self.path}")
                try:
                    member_path = handle.read(path_size).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ArchiveError(f"Invalid UnityWebData member name: {self.path}") from exc
                pure = PurePosixPath(member_path.replace("\\", "/"))
                if pure.is_absolute() or not pure.parts or ".." in pure.parts:
                    raise ArchiveError(f"Unsafe UnityWebData member path: {member_path}")
                if offset < header_size or size < 0 or offset + size > file_size:
                    raise ArchiveError(f"Invalid UnityWebData member extent: {member_path}")
                members.append(UnityDataMember(pure.as_posix(), offset, size))
            if handle.tell() != header_size:
                raise ArchiveError(f"Misaligned UnityWebData header: {self.path}")
        return tuple(members)

    @property
    def members(self) -> tuple[UnityDataMember, ...]:
        return self._members

    def member(self, path: str) -> UnityDataMember:
        normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
        for member in self._members:
            if member.path == normalized:
                return member
        raise KeyError(path)

    def extract(self, member: UnityDataMember | str, destination: Path) -> Path:
        item = self.member(member) if isinstance(member, str) else member
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            with self.path.open("rb") as reader, temporary.open("wb") as writer:
                reader.seek(item.offset)
                remaining = item.size
                while remaining:
                    chunk = reader.read(min(_COPY_CHUNK, remaining))
                    if not chunk:
                        raise ArchiveError(f"Truncated UnityWebData member: {item.path}")
                    writer.write(chunk)
                    remaining -= len(chunk)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def extract_selected(self, paths: Iterable[str], directory: Path) -> dict[str, Path]:
        root = directory.resolve()
        results: dict[str, Path] = {}
        for requested in paths:
            member = self.member(requested)
            target = (root / Path(*PurePosixPath(member.path).parts)).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:  # pragma: no cover - protected by index validation
                raise ArchiveError(f"Unsafe extraction target: {member.path}") from exc
            results[member.path] = self.extract(member, target)
        return results


def prepare_il2cpp_inputs(data: Path, wasm: Path, cache_dir: Path) -> tuple[Path, Path]:
    decoded_data = materialize_payload(data, cache_dir, expected_magic=UNITY_DATA_MAGIC)
    decoded_wasm = materialize_payload(wasm, cache_dir, expected_magic=WASM_MAGIC)
    archive = UnityDataArchive(decoded_data)
    metadata_name = "Il2CppData/Metadata/global-metadata.dat"
    metadata_key = sha256_file(decoded_data)
    metadata = cache_dir / "metadata" / metadata_key / "global-metadata.dat"
    if not metadata.is_file():
        archive.extract(metadata_name, metadata)
    return decoded_wasm, metadata


__all__ = [
    "ArchiveError",
    "UNITY_DATA_MAGIC",
    "UnityDataArchive",
    "UnityDataMember",
    "WASM_MAGIC",
    "materialize_payload",
    "payload_encoding",
    "prepare_il2cpp_inputs",
    "sha256_file",
]
