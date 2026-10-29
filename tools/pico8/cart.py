"""PICO-8 carts inside an exported browser player.

A PICO-8 HTML export embeds every cart as a 32 KiB ROM image in one byte array,
`var _cartdat=[...]`, named in order by `var _cartname=[...]`. The code region of a
ROM is compressed; shrinko8 (the `shrinko` package, run as a subprocess because it
puts its own generic module names on sys.path) converts between ROM and editable
.p8 source and counts tokens. Repacking re-encodes only the carts whose source
changed, so every other cart stays byte-identical.
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tools.paths import temporary_dir

CART_BYTES = 0x8000
DATA_BYTES = 0x4300  # sprites, map, flags, sound and music precede the code
# A cart over the token limit refuses to run ("PROGRAM TOO LARGE"). The limit is one
# comparison in the exported player, next to that message; the count only charges
# tokens*12 bytes against the 2 MB Lua memory budget, so a player may allow more
# (some exports already allow 65535). Compressed code over its limit does not fit in the
# ROM at all.
TOKEN_LIMIT = 8192
LIFTED_TOKEN_LIMIT = 65535
COMPRESSED_LIMIT = 0x3d00

_CARTDAT = re.compile(r"var _cartdat\s*=\s*\[([0-9,\s]*)\]")
_INITIALIZER = re.compile(r"data:application/octet-stream;base64,([A-Za-z0-9+/=]+)")
_GLOBAL_BASE = re.compile(r"GLOBAL_BASE\s*=\s*(\d+)")
_CARTNAME = re.compile(r"var _cartname\s*=\s*\[(.*?)\]", re.S)
_QUOTED = re.compile(r"""['"`]([^'"`]+)['"`]""")
_SECTION = re.compile(r"^__(lua|gfx|gff|label|map|sfx|music|meta:\S+)__$")


class CartError(RuntimeError):
    pass


@dataclass
class Player:
    """The player script, as a file or as `bundle.zip:member`."""

    spec: str
    text: str
    names: list[str]
    roms: list[bytes]
    span: tuple[int, int]

    @classmethod
    def read(cls, spec: str | Path) -> Player:
        spec = str(spec)
        archive, _, member = spec.partition(".zip:")
        if member:
            with zipfile.ZipFile(archive + ".zip") as bundle:
                text = bundle.read(member).decode("utf-8")
        else:
            text = Path(spec).read_text(encoding="utf-8")
        data = _CARTDAT.search(text)
        if data is None:
            raise CartError(f"{spec}: no `var _cartdat=[...]`; not a PICO-8 export")
        raw = bytes(int(value) for value in data.group(1).split(",") if value.strip())
        if len(raw) % CART_BYTES:
            raise CartError(f"{spec}: _cartdat holds {len(raw)} bytes, not whole {CART_BYTES}-byte carts")
        roms = [raw[start:start + CART_BYTES] for start in range(0, len(raw), CART_BYTES)]
        listed = _CARTNAME.search(text)
        names = _QUOTED.findall(listed.group(1)) if listed else []
        if len(names) != len(roms):
            names = [f"cart{index}.p8" for index in range(len(roms))]
        return cls(spec=spec, text=text, names=names, roms=roms, span=data.span(1))

    @property
    def in_archive(self) -> bool:
        return ".zip:" in self.spec

    def with_roms(self, roms: list[bytes]) -> str:
        start, end = self.span
        return self.text[:start] + ",".join(str(byte) for byte in b"".join(roms)) + self.text[end:]

    @property
    def token_limit(self) -> int:
        """The token count the player accepts (TOKEN_LIMIT where its check is not found)."""
        try:
            return int(_token_check(self.text).group(2))
        except CartError:
            return TOKEN_LIMIT

    def with_token_limit(self, limit: int) -> str:
        """The player text with its token check (and the limit its message prints) at `limit`."""
        match = _token_check(self.text)
        start, end = match.span(2)
        head, tail = self.text[:start] + str(limit), self.text[end:]
        printed = re.match(r"[^;]*;\w+\[\w+>>2\]=\w+;\w+\[\w+\+4>>2\]=(\d+);", tail)
        if printed:
            a, b = printed.span(1)
            tail = tail[:a] + str(limit) + tail[b:]
        return head + tail


def _token_check(text: str) -> re.Match:
    """The player's token-limit comparison: the one `if((n|0)>LIMIT)` that reports the
    "program too large" message (asm.js players, which every PICO-8 game here has)."""
    base, data = _GLOBAL_BASE.search(text), _INITIALIZER.search(text)
    if not (base and data):
        raise CartError("the player has no asm.js memory initializer, so its token check cannot be located")
    at = base64.b64decode(data.group(1)).find(b"program too large\0")
    if at < 0:
        raise CartError("the player has no 'program too large' message")
    address = int(base.group(1)) + at
    matches = list(re.finditer(rf"if\(\((\w+)\|0\)>(\d+)\)\{{\w+\({address},\d+\);", text))
    if len(matches) != 1:
        raise CartError(f"expected one token check in the player, found {len(matches)}")
    return matches[0]


def _shrinko(*args: str, cwd: Path) -> str:
    result = subprocess.run([sys.executable, "-m", "shrinko.shrinko8", *args],
                            cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise CartError((result.stderr or result.stdout).strip() or "shrinko8 failed")
    return result.stdout


def _old_format_length(rom: bytes) -> int | None:
    """Characters in old-format (`:c:`) compressed code, counted up to its end mark."""
    pos, length = DATA_BYTES + 8, 0
    while pos < CART_BYTES:
        byte = rom[pos]
        pos += 1
        if byte == 0:
            if pos >= CART_BYTES or rom[pos] == 0:
                return length
            pos, length = pos + 1, length + 1
        elif byte <= 0x3b:
            length += 1
        elif pos < CART_BYTES:
            length += (rom[pos] >> 4) + 2
            pos += 1
    return None


def rom_to_source(rom: bytes) -> str:
    if rom[DATA_BYTES:DATA_BYTES + 4] == b":c:\0":
        # PICO-8 reads old-format code up to its end mark and ignores the stored
        # size, which a builder appending code may not have updated; shrinko8
        # insists on it, so decode a copy with the size corrected.
        length = _old_format_length(rom)
        if length is not None:
            rom = rom[:DATA_BYTES + 4] + length.to_bytes(2, "big") + rom[DATA_BYTES + 6:]
    with tempfile.TemporaryDirectory(dir=temporary_dir("pico8")) as raw:
        work = Path(raw)
        (work / "cart.rom").write_bytes(rom)
        _shrinko("cart.rom", "cart.p8", "-F", "rom", "-f", "p8", cwd=work)
        return (work / "cart.p8").read_text(encoding="utf-8")


def source_to_rom(source: str) -> bytes:
    with tempfile.TemporaryDirectory(dir=temporary_dir("pico8")) as raw:
        work = Path(raw)
        (work / "cart.p8").write_text(source, encoding="utf-8")
        _shrinko("cart.p8", "cart.rom", "-f", "rom", cwd=work)
        rom = (work / "cart.rom").read_bytes()
    if len(rom) != CART_BYTES:
        raise CartError(f"shrinko8 wrote a {len(rom)}-byte ROM, not {CART_BYTES}")
    return rom


def counts(source: str) -> dict[str, int]:
    """tokens, chars and compressed code bytes of one .p8 source."""
    with tempfile.TemporaryDirectory(dir=temporary_dir("pico8")) as raw:
        work = Path(raw)
        (work / "cart.p8").write_text(source, encoding="utf-8")
        output = _shrinko("cart.p8", "--count", "--parsable-count", cwd=work)
    found = {}
    for line in output.splitlines():
        parts = line.split(":")  # count:<cart>:<what>:<value>:<limit>
        if len(parts) == 5 and parts[0] == "count":
            found[parts[2]] = int(parts[3])
    return found


def lua_code(source: str) -> str:
    """The __lua__ section of a .p8 source."""
    lines, inside = [], False
    for line in source.splitlines():
        section = _SECTION.match(line)
        if section:
            inside = section.group(1) == "lua"
        elif inside:
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- name check

_TOKEN = re.compile(r"""
      (?P<comment>--\[(?P<ceq>=*)\[.*?\](?P=ceq)\]|--[^\n]*|//[^\n]*)
    | (?P<long>\[(?P<leq>=*)\[.*?\](?P=leq)\])
    | (?P<string>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<number>0[xX][0-9a-fA-F.]+|0[bB][01.]+|[0-9]+\.?[0-9]*(?:[eE][-+]?[0-9]+)?|\.[0-9]+)
    | (?P<op>\.\.\.|\.\.=?|::|[=~!<>]=|[-+*/%^\\&|]=|[^\s\w])
    """, re.S | re.X)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto",
             "if", "in", "local", "nil", "not", "or", "repeat", "return", "then", "true", "until",
             "while"}


def name_roles(code: str) -> tuple[set[str], Counter[str], dict[str, int]]:
    """(names the code defines, words its strings mention, first line of each bare
    name it reads).

    Defining means a bare assignment, `local`, `function name`, a parameter or a loop
    variable. Strings are counted apart: carts also create globals from string names
    (`_ENV[name]=...`, objects built from `[[c="name"]]`), but a string can as well
    look a name up, so a mention is not proof of a definition.
    """
    tokens: list[tuple[str, str, int]] = []
    line, counted = 1, 0
    for match in _TOKEN.finditer(code):
        kind, value = match.lastgroup, match.group()
        line += code.count("\n", counted, match.start())
        counted = match.start()
        if kind in ("string", "long"):
            tokens.append(("string", value, line))
        elif kind == "name":
            tokens.append(("keyword" if value in _KEYWORDS else "name", value, line))
        elif kind in ("op", "number"):
            tokens.append((kind, value, line))

    defined: set[str] = set()
    mentioned: Counter[str] = Counter()
    used: dict[str, int] = {}

    def value(index: int) -> str:
        return tokens[index][1] if 0 <= index < len(tokens) else ""

    def is_name(index: int) -> bool:
        return 0 <= index < len(tokens) and tokens[index][0] == "name"

    for index, (kind, text, at) in enumerate(tokens):
        if kind == "string":
            mentioned.update(_WORD.findall(text))
        elif kind == "name":
            if value(index - 1) not in (".", ":"):
                used.setdefault(text, at)
        elif text == "function":
            # `function f(a, b)` defines f, a and b; `function t.f()` reads t.
            cursor, chain = index + 1, []
            while is_name(cursor):
                chain.append(value(cursor))
                cursor += 1
                if value(cursor) not in (".", ":"):
                    break
                cursor += 1
            if len(chain) == 1:
                defined.add(chain[0])
            if value(cursor) == "(":
                cursor += 1
                while is_name(cursor) or value(cursor) in (",", "..."):
                    if is_name(cursor):
                        defined.add(value(cursor))
                    cursor += 1
        elif text in ("local", "for"):
            cursor = index + 1
            while is_name(cursor):
                defined.add(value(cursor))
                if value(cursor + 1) != ",":
                    break
                cursor += 2
        elif text == "=":
            # Bare names in the target list `a, t.k, b = ...` are assigned.
            cursor = index - 1
            while is_name(cursor):
                head = cursor
                while value(head - 1) in (".", ":") and is_name(head - 2):
                    head -= 2
                if head == cursor:
                    defined.add(value(cursor))
                if value(head - 1) != ",":
                    break
                cursor = head - 2
    return defined, mentioned, used


def lost_definitions(before: str, after: str) -> dict[str, int]:
    """Names the edited code still reads, with the first line that reads them, that
    may no longer be defined: deleting a definition while a use elsewhere survives
    crashes the cart at run time. A name counts when it lost its last definition, or
    when it has none in the code and fewer strings mention it than before (one of
    them may have created it)."""
    defined_before, mentioned_before, _ = name_roles(before)
    defined_after, mentioned_after, used_after = name_roles(after)
    return {name: line for name, line in sorted(used_after.items())
            if name not in defined_after
            and (name in defined_before or mentioned_after[name] < mentioned_before[name])}


__all__ = [
    "CART_BYTES",
    "COMPRESSED_LIMIT",
    "CartError",
    "DATA_BYTES",
    "LIFTED_TOKEN_LIMIT",
    "Player",
    "TOKEN_LIMIT",
    "counts",
    "lost_definitions",
    "lua_code",
    "name_roles",
    "rom_to_source",
    "source_to_rom",
]
