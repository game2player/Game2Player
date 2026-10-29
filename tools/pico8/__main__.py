"""Command line interface for PICO-8 carts in an exported browser player.

    python -m tools.pico8 unpack games/benchmark/<game>/<player>.js .cache/pico8/<game>
    # edit the .p8 files
    python -m tools.pico8 pack games/benchmark/<game>/<player>.js .cache/pico8/<game>
    python -m tools.pico8 check games/benchmark/<game>/<player>.js [--against <original>.js]
    python -m tools.pico8 lift-token-limit games/benchmark/<game>/<player>.js

A player inside a bundle is read as `.../_bundle_001.zip:<member>.js`; pack it to a
file with --out.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cart import (
    COMPRESSED_LIMIT,
    CartError,
    DATA_BYTES,
    LIFTED_TOKEN_LIMIT,
    Player,
    counts,
    lost_definitions,
    lua_code,
    rom_to_source,
    source_to_rom,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.pico8")
    commands = parser.add_subparsers(dest="command", required=True)

    unpack = commands.add_parser("unpack", help="write every cart of a player as .p8 source")
    unpack.add_argument("player")
    unpack.add_argument("cart_dir", type=Path)
    unpack.add_argument("--force", action="store_true", help="overwrite edited .p8 files")

    pack = commands.add_parser("pack", help="put edited .p8 carts back into the player")
    pack.add_argument("player")
    pack.add_argument("cart_dir", type=Path)
    pack.add_argument("--out", type=Path, help="write here instead of over the player")
    pack.add_argument("--allow-undefined", action="append", default=[], metavar="NAME",
                      help="a name the edit may leave undefined (repeatable)")

    check = commands.add_parser("check", help="cart limits, and names an edit left undefined")
    check.add_argument("player")
    check.add_argument("--against", help="the player before the edit")

    lift = commands.add_parser("lift-token-limit",
                               help="let the player run carts over 8192 tokens (the count only charges memory)")
    lift.add_argument("player")
    lift.add_argument("--limit", type=int, default=LIFTED_TOKEN_LIMIT)
    lift.add_argument("--out", type=Path, help="write here instead of over the player")
    return parser


def _report(name: str, source: str, limit: int) -> dict[str, int]:
    found = counts(source)
    over = " (over the limit)" if found.get("tokens", 0) > limit else ""
    print(f"{name}: {found.get('tokens', 0)}/{limit} tokens{over}, "
          f"{found.get('compressed', 0)}/{COMPRESSED_LIMIT} compressed code bytes")
    return found


def _problems(found: dict[str, int], source: str, before: str | None, allowed: set[str],
              limit: int) -> list[str]:
    problems = []
    tokens = found.get("tokens", 0)
    if tokens > limit and before is not None:
        # An edit may not push a cart over the limit; one that was over and runs anyway
        # may keep its size. Without a before there is no edit to judge.
        if tokens > counts(before).get("tokens", 0):
            problems.append(f"{tokens} tokens > {limit}: the player refuses to run it (PROGRAM TOO LARGE); "
                            "lift-token-limit raises the limit")
    if found.get("compressed", 0) > COMPRESSED_LIMIT:
        problems.append(f"compressed code does not fit in the ROM ({found['compressed']} > {COMPRESSED_LIMIT} bytes)")
    if before is not None:
        for name, line in lost_definitions(lua_code(before), lua_code(source)).items():
            if name not in allowed:
                problems.append(f"'{name}' is still read (code line {line}) but its definition, or a"
                                f" string naming it, was removed; pass --allow-undefined {name} if intended")
    return problems


def _unpack(args: argparse.Namespace) -> int:
    player = Player.read(args.player)
    args.cart_dir.mkdir(parents=True, exist_ok=True)
    for name, rom in zip(player.names, player.roms):
        source = rom_to_source(rom)
        path = args.cart_dir / name
        if path.exists() and path.read_text(encoding="utf-8") != source and not args.force:
            raise CartError(f"{path} differs from the player's cart; pack it or pass --force")
        path.write_text(source, encoding="utf-8")
        _report(name, source, player.token_limit)
    print(f"wrote {len(player.roms)} carts to {args.cart_dir}")
    return 0


def _pack(args: argparse.Namespace) -> int:
    player = Player.read(args.player)
    if player.in_archive and args.out is None:
        raise CartError("the player is inside a bundle; pass --out")
    roms, changed, failed = list(player.roms), [], False
    for index, (name, rom) in enumerate(zip(player.names, player.roms)):
        source = (args.cart_dir / name).read_text(encoding="utf-8")
        before = rom_to_source(rom)
        if source == before:
            continue
        problems = _problems(_report(name, source, player.token_limit), source, before,
                             set(args.allow_undefined), player.token_limit)
        for problem in problems:
            print(f"  {name}: {problem}")
        failed |= bool(problems)
        if problems:
            continue
        new = source_to_rom(source)
        if lua_code(rom_to_source(new)) != lua_code(source):
            raise CartError(f"{name}: the re-encoded code does not decode to the edited source")
        if new[:DATA_BYTES] != rom[:DATA_BYTES]:
            print(f"  {name}: sprite, map or sound data changed too")
        roms[index] = new
        changed.append(name)
    if failed:
        sys.stdout.flush()
        print("nothing written", file=sys.stderr)
        return 1
    if not changed:
        print("no cart changed")
        return 0
    output = args.out or Path(player.spec)
    output.write_text(player.with_roms(roms), encoding="utf-8")
    print(f"re-encoded {', '.join(changed)}; other carts unchanged; wrote {output}")
    return 0


def _check(args: argparse.Namespace) -> int:
    player = Player.read(args.player)
    original = Player.read(args.against) if args.against else None
    if original is not None and original.names != player.names:
        raise CartError(f"cart names differ: {original.names} vs {player.names}")
    failed = False
    for index, (name, rom) in enumerate(zip(player.names, player.roms)):
        source = rom_to_source(rom)
        before = None
        if original is not None:
            if original.roms[index] == rom:
                continue
            before = rom_to_source(original.roms[index])
        for problem in _problems(_report(name, source, player.token_limit), source, before, set(),
                                 player.token_limit):
            print(f"  {name}: {problem}")
            failed = True
    return 1 if failed else 0


def _lift(args: argparse.Namespace) -> int:
    player = Player.read(args.player)
    if player.in_archive and args.out is None:
        raise CartError("the player is inside a bundle; pass --out")
    current = player.token_limit
    if current >= args.limit:
        print(f"the player already accepts {current} tokens")
        return 0
    text = player.with_token_limit(args.limit)
    output = args.out or Path(player.spec)
    output.write_text(text, encoding="utf-8")
    lifted = Player.read(output)
    if lifted.token_limit != args.limit or lifted.roms != player.roms:
        raise CartError(f"{output}: the rewritten player does not read back as intended")
    print(f"token limit {current} -> {args.limit}; wrote {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return {"unpack": _unpack, "pack": _pack, "check": _check, "lift-token-limit": _lift}[args.command](args)
    except (CartError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
