"""Apply the existing v1.8 observation hook and offline HTML integration.

Offsets refer to the SHA-256-pinned, HTTP-decoded upstream Wasm. All original
instructions and assets are reused; only nine injected bytes and two
Wasm length fields are written. No compressed-container delta is used.
"""
import hashlib
from pathlib import Path
import sys


def uleb(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def patch(root: Path) -> None:
    wasm = root / "Build/Bonnie's Bakery v1.8 (WebGL).wasm.gz"
    original = wasm.read_bytes()
    if hashlib.sha256(original).hexdigest() != "1bfb1889b9e720a518ab3a36069a53069c80ad3147d286dea34b3e55edf467a5":
        raise ValueError("Unexpected original v1.8 Wasm")

    # Fungus.SetVariable.OnEnter: function 101252, code-body index 100744,
    # table slot 19600. Its 54-byte body starts at 24141097 with zero locals.
    # Insert after the local declarations: local.get 0; i32.const 190190;
    # call 478 (the existing a.Vh / JS_Log_Dump import). The adapter intercepts
    # only this reserved tag and observes commands without changing game state.
    hook = b"\x20\x00\x41" + uleb(190190) + b"\x10" + uleb(478)
    edits = [
        (650232, 650236, uleb(30360954 + len(hook))),  # code-section length
        (24141096, 24141097, uleb(54 + len(hook))),    # function-body length
        (24141098, 24141098, hook),                  # first instruction
    ]
    patched = original
    for start, end, replacement in reversed(edits):
        patched = patched[:start] + replacement + patched[end:]
    if hashlib.sha256(patched).hexdigest() != "57fcea653b45d67323f7b284d7d02ad2da2c0dbc45113aeca7517f2b4216aadb":
        raise ValueError("Wasm hook differs from the validated reference")
    wasm.write_bytes(patched)

    entry = root / "index.html"
    html = entry.read_text(encoding="utf-8")
    changes = [
        ('\t\t<script src="Build/', '\t\t<script src="game_api.js"></script>\n\t\t<script src="Build/'),
        ('\t\t\t\tcanvas = instance.Module.canvas;',
         '\t\t\t\twindow.unityInstance = instance;\n                canvas = instance.Module.canvas;'),
        ('<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', ''),
    ]
    for anchor, replacement in changes:
        if html.count(anchor) != 1:
            raise ValueError(f"Unexpected upstream HTML anchor: {anchor!r}")
        html = html.replace(anchor, replacement)
    entry.write_text(html, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch.py GAME_DIRECTORY")
    patch(Path(sys.argv[1]))
