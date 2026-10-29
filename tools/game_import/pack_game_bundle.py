"""Pack a static game tree into Git-friendly ZIP shards.

The launcher serves these shards directly, so no extraction is required at
runtime. Primary/fallback entry points and runtime configuration remain visible
in the game directory.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

from tools.paths import cache_dir, temporary_dir


REPO = Path(__file__).resolve().parents[2]
BENCHMARK = REPO / "games" / "benchmark"
BACKUP_ROOT = cache_dir("tmp", "bundles", "unpacked")

KEEP_VISIBLE = {
    "_headers.json",
    "game_api.js",
    "capture_api.js",
    "index.html",
    "official.html",
    "official_game_api.js",
}


def collect_payload(game_dir: Path) -> list[tuple[Path, Path]]:
    payload: list[tuple[Path, Path]] = []
    for path in sorted(game_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(game_dir)
        if relative.as_posix() in KEEP_VISIBLE:
            continue
        if len(relative.parts) == 1 and relative.name.startswith("_bundle_"):
            continue
        payload.append((path, relative))
    return payload


def partition(
    payload: list[tuple[Path, Path]], target_bytes: int
) -> list[list[tuple[Path, Path]]]:
    shards: list[list[tuple[Path, Path]]] = []
    current: list[tuple[Path, Path]] = []
    estimated_size = 0
    for item in payload:
        path, relative = item
        # Allow room for the local and central-directory records.
        item_size = path.stat().st_size + 256 + len(relative.as_posix()) * 2
        if current and estimated_size + item_size > target_bytes:
            shards.append(current)
            current = []
            estimated_size = 0
        current.append(item)
        estimated_size += item_size
    if current:
        shards.append(current)
    return shards


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("game_name")
    parser.add_argument("--target-mib", type=int, default=60)
    parser.add_argument("--max-mib", type=int, default=95)
    args = parser.parse_args()

    game_dir = BENCHMARK / args.game_name
    if not game_dir.is_dir():
        raise FileNotFoundError(game_dir)
    if list(game_dir.glob("_bundle_*.zip")):
        raise FileExistsError(f"Game is already packaged: {game_dir}")

    payload = collect_payload(game_dir)
    if not payload:
        raise RuntimeError(f"No payload files found in {game_dir}")
    target_bytes = args.target_mib * 1024 * 1024
    max_bytes = args.max_mib * 1024 * 1024
    shards = partition(payload, target_bytes)
    build_root = temporary_dir("bundles")
    build_root.mkdir(parents=True, exist_ok=True)
    build_dir = Path(
        tempfile.mkdtemp(prefix=f"{args.game_name}-bundle-", dir=build_root)
    )

    archive_records: list[dict[str, object]] = []
    all_members: set[str] = set()
    try:
        for index, shard in enumerate(shards, start=1):
            name = f"_bundle_{index:03d}.zip"
            archive_path = build_dir / name
            with zipfile.ZipFile(
                archive_path,
                "w",
                compression=zipfile.ZIP_STORED,
                allowZip64=False,
                strict_timestamps=False,
            ) as archive:
                for source, relative in shard:
                    member = relative.as_posix()
                    if member in all_members:
                        raise RuntimeError(f"Duplicate payload member: {member}")
                    archive.write(source, member)
                    all_members.add(member)
            size = archive_path.stat().st_size
            if size >= max_bytes:
                raise RuntimeError(
                    f"{name} is {size / 1024 / 1024:.1f} MiB, "
                    f"above the {args.max_mib} MiB limit"
                )
            with zipfile.ZipFile(archive_path, "r") as archive:
                corrupt = archive.testzip()
                if corrupt:
                    raise RuntimeError(f"CRC verification failed: {name}:{corrupt}")
                member_count = len(
                    [info for info in archive.infolist() if not info.is_dir()]
                )
            archive_records.append(
                {
                    "name": name,
                    "bytes": size,
                    "members": member_count,
                }
            )

        expected_members = {relative.as_posix() for _, relative in payload}
        if all_members != expected_members:
            raise RuntimeError("Packaged member inventory does not match source tree")

        backup = BACKUP_ROOT / args.game_name
        if backup.exists():
            raise FileExistsError(f"Unpacked backup already exists: {backup}")
        backup.mkdir(parents=True)

        # Install verified archives before moving the loose files. During this
        # short transition, loose files take precedence; if interrupted, the
        # launcher can still satisfy missing requests from the installed shards.
        for record in archive_records:
            shutil.move(
                str(build_dir / str(record["name"])),
                str(game_dir / str(record["name"])),
            )

        top_level_items = sorted({relative.parts[0] for _, relative in payload})
        for name in top_level_items:
            source = game_dir / name
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))

        shutil.rmtree(backup)
        print(
            f"Packed {len(payload)} files into {len(archive_records)} shards; "
            f"{sum(int(record['bytes']) for record in archive_records) / 1024 / 1024:.1f} MiB"
        )
    finally:
        if build_dir.exists() and not any(build_dir.iterdir()):
            build_dir.rmdir()


if __name__ == "__main__":
    main()
