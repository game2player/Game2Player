"""Download original games and apply the accompanying PlayerBench adapters."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from filelock import FileLock
import requests
import yaml

HERE = Path(__file__).resolve().parent


def child(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError(f"Unsafe relative path: {relative!r}")
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes destination: {relative!r}")
    return target


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(item: dict, cache: Path) -> Path:
    expected = item.get("sha256", "")
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError(f"Missing original-file SHA-256 for {item.get('path')}")
    url = item["url"]
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"Unsupported download URL: {url}")
    path = cache / expected
    if path.is_file() and digest(path) == expected:
        return path
    cache.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(3):
        temporary = None
        try:
            with requests.get(url, headers=item.get("headers", {}), stream=True,
                              timeout=(20, 180)) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
                    temporary = Path(stream.name)
                    for block in response.iter_content(1024 * 1024):
                        stream.write(block)
            if digest(temporary) != expected:
                raise ValueError(f"Original version changed or wrong download: {url}")
            os.replace(temporary, path)
            return path
        except ValueError:
            raise
        except (requests.RequestException, OSError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise RuntimeError(f"Download failed after 3 attempts: {url}: {last}")


def unpack_zip(source: Path, destination: Path, prefix: str = "") -> None:
    prefix = prefix.rstrip("/") + "/" if prefix else ""
    extracted = 0
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            if not member.filename.startswith(prefix) or member.is_dir():
                continue
            name = member.filename[len(prefix):]
            if not name:
                continue
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"Archive contains a symlink: {member.filename}")
            target = child(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
            extracted += 1
    if not extracted:
        raise ValueError(f"Archive prefix matched no files: {prefix!r}")


def copy_original(item: dict, source: Path, stage: Path) -> None:
    if item.get("unpack") == "zip":
        unpack_zip(source, child(stage, item.get("path", ".")), item.get("strip_prefix", ""))
    elif "unpack" in item:
        raise ValueError(f"Unsupported archive type: {item['unpack']}")
    else:
        target = child(stage, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def populate(package: Path, manifest: dict, stage: Path, cache: Path) -> None:
    originals = manifest.get("downloads", [])
    if not originals and "original_source" not in manifest:
        raise ValueError("Provide public original downloads or a bundled original source")
    # Fetch a few assets at a time. Apply files in manifest order, including archives.
    with ThreadPoolExecutor(max_workers=6) as pool:
        for start in range(0, len(originals), 6):
            batch = originals[start:start + 6]
            futures = [pool.submit(download, item, cache) for item in batch]
            for item, future in zip(batch, futures):
                copy_original(item, future.result(), stage)
    if "original_source" in manifest:
        source = child(HERE, manifest["original_source"])
        if not source.is_dir() or not source.resolve().is_relative_to((HERE / "originals").resolve()):
            raise ValueError("original_source must name an included original under originals/")
        shutil.copytree(source, stage, dirs_exist_ok=True)
    patch = package / "patch.py"
    if patch.is_file():
        subprocess.run([sys.executable, str(patch), str(stage)], cwd=package, check=True,
                       timeout=900)
    overlay = package / "overlay"
    if overlay.exists():
        for source in overlay.rglob("*"):
            if source.is_symlink():
                raise ValueError(f"Overlay cannot contain a symlink: {source}")
            if source.is_file():
                target = child(stage, source.relative_to(overlay).as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    entry = child(stage, manifest.get("entrypoint", "index.html"))
    if not entry.is_file():
        raise ValueError(f"Missing installed entrypoint: {entry}")


def install(game_id: str, root: Path, cache: Path, replace: bool) -> str:
    package = HERE / "patches" / game_id
    manifest = json.loads((package / "source.json").read_text())
    definition = yaml.safe_load((HERE / "tasks" / f"{game_id}.yaml").read_text())
    if manifest["game_id"] != game_id or definition["game"]["game_name"] != game_id:
        raise ValueError("Game ID differs between the manifest and task definitions")

    game = root / "games" / "benchmark" / game_id
    catalog = root / "catalog"
    profile = package / "unity.yaml"
    game_yaml = catalog / "games" / f"{game_id}.yaml"
    task_dir = catalog / "tasks" / game_id
    targets = [game_yaml, task_dir]
    if profile.is_file():
        targets.append(catalog / "unity" / f"{game_id}.yaml")
    targets.append(game)  # Publish the playable build last.
    lock_dir = root / ".cache" / "playerbench" / "locks"
    lock_dir.mkdir(exist_ok=True, parents=True)
    with FileLock(lock_dir / f"{game_id}.lock"), ExitStack() as cleanup:
        if not replace:
            ready = (game / ".playerbench-installed").is_file() and (game / manifest["entrypoint"]).is_file()
            if ready and all(p.exists() for p in targets) and all(
                (task_dir / f"{task['task_id']}.yaml").is_file() for task in definition["tasks"]
            ):
                return "Already installed"
            if any(p.exists() for p in targets):
                raise FileExistsError("Local game or catalog already exists. Use --replace to reinstall this game.")

        # Stage on each destination filesystem, so renaming needs no cross-device copy.
        staged = {}
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            scratch = cleanup.enter_context(tempfile.TemporaryDirectory(dir=target.parent, prefix=f".{game_id}-"))
            staged[target] = Path(scratch) / "new"
        staged[game_yaml].write_text(yaml.safe_dump(definition["game"], allow_unicode=True, sort_keys=False))
        staged[task_dir].mkdir()
        for task in definition["tasks"]:
            child(staged[task_dir], f"{task['task_id']}.yaml").write_text(
                yaml.safe_dump(task, allow_unicode=True, sort_keys=False))
        if profile.is_file():
            shutil.copyfile(profile, staged[catalog / "unity" / f"{game_id}.yaml"])
        staged[game].mkdir()
        populate(package, manifest, staged[game], cache)
        (staged[game] / ".playerbench-installed").touch()

        backups = {}
        published = []
        try:
            for target, source in staged.items():
                if target.exists():
                    previous = source.with_name("previous")
                    target.rename(previous)
                    backups[target] = previous
                source.rename(target)
                published.append(target)
        except BaseException:
            for target in reversed(published):
                target.rename(staged[target])
            for target, previous in backups.items():
                previous.rename(target)
            raise
    return "Installed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("games", nargs="*", metavar="GAME_ID", help="Install only these games (default: all 50)")
    parser.add_argument("--replace", action="store_true", help="Replace existing local builds and task catalogs")
    args = parser.parse_args()
    known = sorted((p.stem for p in (HERE / "tasks").glob("*.yaml")), key=lambda name: int(name.split("_")[0]))
    for name in args.games:
        if name not in known:
            parser.error(f"Unknown game ID: {name}")
    games = list(dict.fromkeys(args.games)) or known
    root = HERE.parents[1]
    cache = root / ".cache" / "playerbench" / "downloads"
    failed = []
    for index, game_id in enumerate(games, 1):
        print(f"[{index}/{len(games)}] {game_id}", flush=True)
        try:
            print("  " + install(game_id, root, cache, args.replace), flush=True)
        except Exception as exc:  # Each game is an independent installation.
            failed.append(game_id)
            print(f"  Skipped {game_id} ({type(exc).__name__}): {exc}", flush=True)
    print(f"Ready: {len(games) - len(failed)}/{len(games)} games.")
    if failed:
        options = ["--replace"] if args.replace else []
        print("Retry: python benchmark/playerbench/install.py " + " ".join(options + failed))
    return int(bool(failed))


if __name__ == "__main__":
    sys.exit(main())
