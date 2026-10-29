"""Extract one directory from an Electron ASAR archive without extra packages."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any, BinaryIO


def read_header(archive: BinaryIO) -> tuple[dict[str, Any], int]:
    archive.seek(4)
    header_pickle_size = struct.unpack("<I", archive.read(4))[0]
    archive.seek(12)
    header_json_size = struct.unpack("<I", archive.read(4))[0]
    header = json.loads(archive.read(header_json_size))
    return header["files"], 8 + header_pickle_size


def descend(files: dict[str, Any], path: str) -> dict[str, Any]:
    node: dict[str, Any] = {"files": files}
    for part in Path(path).parts:
        node = node["files"][part]
        if "files" not in node:
            raise ValueError(f"{path!r} is not a directory in the archive")
    return node


def extract_tree(
    archive: BinaryIO,
    node: dict[str, Any],
    data_offset: int,
    output: Path,
    extraction_root: Path | None = None,
) -> tuple[int, int]:
    root = (extraction_root or output).resolve()
    count = 0
    total_size = 0
    for name, entry in node["files"].items():
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or name in {".", ".."}
        ):
            raise ValueError(f"Unsafe ASAR member name: {name!r}")
        destination = (output / name).resolve()
        if not destination.is_relative_to(root):
            raise ValueError(f"ASAR member escapes output directory: {name!r}")
        if "files" in entry:
            destination.mkdir(parents=True, exist_ok=True)
            child_count, child_size = extract_tree(
                archive, entry, data_offset, destination, root
            )
            count += child_count
            total_size += child_size
            continue
        if "link" in entry or entry.get("unpacked"):
            raise ValueError(f"Unsupported ASAR entry: {destination}")
        size = int(entry["size"])
        offset = data_offset + int(entry["offset"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        archive.seek(offset)
        remaining = size
        with destination.open("wb") as target:
            while remaining:
                chunk = archive.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise EOFError(f"Unexpected EOF extracting {destination}")
                target.write(chunk)
                remaining -= len(chunk)
        count += 1
        total_size += size
    return count, total_size


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("subdirectory")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    with args.archive.open("rb") as archive:
        files, data_offset = read_header(archive)
        node = descend(files, args.subdirectory)
        count, total_size = extract_tree(
            archive, node, data_offset, args.output
        )
    print(
        f"Extracted {count} files ({total_size / (1024 * 1024):.1f} MiB) "
        f"from {args.subdirectory!r} to {args.output}"
    )


if __name__ == "__main__":
    main()
