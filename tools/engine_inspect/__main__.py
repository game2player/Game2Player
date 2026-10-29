"""Inspect assets without launching games, unpacking bundles, or downloading tools."""

from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import sys
import zipfile
import subprocess

from tools.engine_tools import DISTRIBUTIONS, tool_path, tools_dir
from .assets import iter_assets

MARKERS = {
    "unity_webgl": ("UnityLoader", "createUnityInstance", ".data.unityweb"),
    "pico8": ("pico8_gpio", "_cartdat", "p8_run_cart"),
    "flash": ("RufflePlayer", ".swf"),
    "godot": ("GODOT_CONFIG", ".pck", "Godot Engine"),
    "construct": ("cr_getC2Runtime", "c2runtime.js", "c3runtime.js"),
    "gamemaker_html5": ("gml_Object_", "gml_Script_", "GameMaker"),
    "defold": ("dmloader.js", "archive_files.json", "GetUserPersistentDataRoot"),
}
def local_tools() -> dict:
    out = {}
    for name in ("ffdec", "gdre", "java", "dotnet"):
        try:
            path = tool_path(name, unpack=False)
            out[name] = {"path": str(path), "unpacked": path.is_file()}
            if name in DISTRIBUTIONS:
                out[name]["distribution"] = str(DISTRIBUTIONS[name])
            else:
                args = ["-version"] if name == "java" else ["--list-runtimes"]
                result = subprocess.run([str(path), *args], capture_output=True, text=True, timeout=15)
                out[name]["runtime_info"] = (result.stdout + result.stderr).strip()
                out[name]["exit_code"] = result.returncode
                if name == "dotnet":
                    out[name]["net6_available"] = result.returncode == 0 and any(
                        line.startswith("Microsoft.NETCore.App 6.0.") for line in result.stdout.splitlines())
        except subprocess.TimeoutExpired:
            out[name] = {"path": str(path), "error": "runtime check timed out"}
        except OSError as exc:
            out[name] = {"path": None, "error": str(exc)}
    for package in ("UnityPy", "shrinko"):
        try:
            out[package] = {"version": metadata.version(package)}
        except metadata.PackageNotFoundError:
            out[package] = {"version": None}
    return {"directory": str(tools_dir()), "tools": out,
            "note": "Local availability and runtime versions; no downloads or game compatibility checks."}


def inspect(root: Path, limit: int, max_bytes: int) -> dict:
    assets = list(iter_assets(root))
    evidence = {engine: [] for engine in MARKERS}
    remaining = 16 * 1024 * 1024
    skipped = 0
    for asset in assets:
        text = ""
        if Path(asset.name).suffix in {".js", ".html", ".json"}:
            if remaining > 0:
                with asset.open() as stream:
                    data = stream.read(min(max_bytes, remaining))
                remaining -= len(data)
                text = data.decode("utf-8", errors="replace")
                skipped += int(len(data) < asset.size)
            else:
                skipped += 1
        for engine, markers in MARKERS.items():
            if any(marker in asset.name or marker in text for marker in markers):
                if len(evidence[engine]) < 5:
                    evidence[engine].append(asset.name)
    evidence = {engine: names for engine, names in evidence.items() if names}
    return {"root": str(root.resolve()), "engine_evidence": evidence,
            "note": "Static hints, not a confirmed engine or a working state interface.",
            "text_assets_partly_or_not_scanned": skipped,
            "files": [{"name": a.name, "bytes": a.size, "source": a.spec} for a in assets[:limit]],
            "file_count": len(assets), "omitted_files": max(0, len(assets) - limit)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="locate optional local analysis tools; never download")
    sub = commands.add_parser("inspect", help="list assets and engine hints, including ZIP shards")
    sub.add_argument("source", help="game directory")
    sub.add_argument("--limit", type=int, default=50, help="maximum output entries")
    sub.add_argument("--max-bytes", type=int, default=2 * 1024 * 1024,
                     help="maximum bytes scanned per text asset (default 2 MiB)")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            out = local_tools()
        else:
            if not 1 <= args.limit <= 1000 or not 1 <= args.max_bytes <= 64 * 1024 * 1024:
                raise ValueError("limit must be 1..1000; max-bytes must be 1..67108864")
            out = inspect(Path(args.source), args.limit, args.max_bytes)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"engine-inspect: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
