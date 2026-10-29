"""Command line interface for Unity WebGL inspection."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .analysis import analyze_with_il2cppdumper, prepare_build
from .audit import audit_profiles
from .cache import default_cache_dir
from .discovery import discover_build
from .profile import DEFAULT_PROFILE_DIR, load_profile
from .symbols import describe_type, search_symbols
from .assets import json_default, list_assets, load_assets, show_asset


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.unity_webgl")
    parser.add_argument("--cache-dir", type=Path, default=None, help="analysis cache (default: <repo>/.cache/unity_webgl)")
    commands = parser.add_subparsers(dest="command", required=True)

    discover = commands.add_parser("discover", help="locate Unity build artifacts")
    discover.add_argument("game_dir", type=Path)
    discover.add_argument("--fingerprint", action="store_true")

    prepare = commands.add_parser("prepare", help="decode WASM and extract IL2CPP metadata")
    prepare.add_argument("game_dir", type=Path)

    analyze = commands.add_parser("analyze", help="run Il2CppDumper and index symbols")
    analyze.add_argument("game_dir", type=Path)
    analyze.add_argument(
        "--dumper",
        type=Path,
        help="Il2CppDumper.dll (defaults to the vendored copy, then the external cache)",
    )
    analyze.add_argument("--dotnet", type=Path)
    analyze.add_argument("--force", action="store_true")

    query = commands.add_parser("query", help="search one symbols.sqlite3")
    query.add_argument("database", type=Path)
    query.add_argument("pattern")
    query.add_argument("--limit", type=int, default=100)
    query.add_argument(
        "--kind",
        choices=("all", "type", "field", "enum", "method", "method_info", "metadata"),
        default="all",
    )

    show_type = commands.add_parser("show-type", help="show one type and all runtime locators")
    show_type.add_argument("database", type=Path)
    show_type.add_argument("type_name")
    show_type.add_argument("--limit", type=int, default=200)

    validate = commands.add_parser("validate-profile", help="validate a Unity profile")
    validate.add_argument("profile", type=Path)

    audit = commands.add_parser(
        "audit-profiles",
        help="summarize state sources and address roots across reviewed profiles",
    )
    audit.add_argument("directory", nargs="?", type=Path, default=DEFAULT_PROFILE_DIR)

    assets = commands.add_parser("assets", help="read serialized resources with UnityPy; no WASM analysis")
    asset_commands = assets.add_subparsers(dest="asset_command", required=True)
    listing = asset_commands.add_parser("list", help="list resource names and object IDs")
    listing.add_argument("source", type=Path, help="Unity .data, AssetBundle or serialized asset file")
    listing.add_argument("--type", dest="type_name")
    listing.add_argument("--name", help="case-insensitive name substring")
    listing.add_argument("--limit", type=int, default=50)
    showing = asset_commands.add_parser("show", help="inspect one object's serialized data")
    showing.add_argument("source", type=Path)
    showing.add_argument("--path-id", type=int, required=True)
    showing.add_argument("--file", dest="file_name", help="asset file name from assets list, when IDs overlap")
    showing.add_argument("--output", type=Path, help="export a TextAsset's original bytes to this file")
    return parser


VENDORED_DUMPER = Path(__file__).parent / "vendor" / "il2cppdumper" / "Il2CppDumper.dll"


def _first_existing(*candidates: Path) -> Path:
    """The first candidate that exists, else the last one for a clear error."""
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "discover":
        payload = discover_build(args.game_dir).as_dict(include_fingerprint=args.fingerprint)
    elif args.command == "prepare":
        payload = prepare_build(args.game_dir, args.cache_dir).as_dict()
    elif args.command == "analyze":
        prepared = prepare_build(args.game_dir, args.cache_dir)
        backend_root = (args.cache_dir or default_cache_dir()) / "backends"
        # The dumper is vendored so a wiped cache cannot take symbol analysis with
        # it; the cached copy still wins if someone has staged a newer one there.
        dumper = args.dumper or _first_existing(
            backend_root / "il2cppdumper" / "Il2CppDumper.dll",
            VENDORED_DUMPER,
        )
        dotnet = args.dotnet
        if dotnet is None and os.environ.get("GAMEENV_DOTNET"):
            dotnet = Path(os.environ["GAMEENV_DOTNET"]).expanduser().resolve()
        if dotnet is None:
            cached_dotnet = backend_root / "dotnet" / "dotnet"
            dotnet = cached_dotnet if cached_dotnet.is_file() else None
        database = analyze_with_il2cppdumper(
            prepared,
            dumper,
            dotnet=dotnet,
            force=args.force,
        )
        payload = {**prepared.as_dict(), "symbols": str(database)}
    elif args.command == "query":
        payload = search_symbols(
            args.database,
            args.pattern,
            limit=max(1, args.limit),
            kind=args.kind,
        )
    elif args.command == "show-type":
        payload = describe_type(args.database, args.type_name, limit=max(1, args.limit))
    elif args.command == "validate-profile":
        payload = load_profile(args.profile)
    elif args.command == "assets":
        try:
            if args.asset_command == "list" and args.limit < 1:
                raise ValueError("--limit must be positive")
            environment = load_assets(args.source)
            if args.asset_command == "list":
                payload = list_assets(environment, type_name=args.type_name, name=args.name, limit=args.limit)
            else:
                payload = show_asset(environment, path_id=args.path_id, file_name=args.file_name, output=args.output)
            print(json.dumps(payload, indent=2, ensure_ascii=True, default=json_default))
            return 0
        except Exception as exc:
            print(f"unity-assets: {exc}", file=sys.stderr)
            return 2
    else:
        payload = audit_profiles(args.directory)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
