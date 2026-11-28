"""Apply the existing PlayerBench hooks to the author's Petal Quest 1.2 export.

Requires shrinko==1.2.7.1 (shrinko8 cartridge codec); never reads a reference build.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from PIL import Image


HERE = Path(__file__).resolve().parent


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected one patch anchor: {old[:80]!r}")
    return text.replace(old, new, 1)


def codec(work, *args):
    subprocess.run([sys.executable, "-m", "shrinko.shrinko8", *args],
                   cwd=work, check=True, capture_output=True)


def main(root):
    manifest = json.loads((HERE / "source.json").read_text())
    for item in manifest["downloads"]:
        if hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Unexpected original: {item['path']}")
    path = root / "petal_quest_1.2.js"
    engine = root / "engine-export.js"
    cart = root / "petal_quest-12.p8.png"
    with Image.open(cart) as image:
        # PICO-8 stores one cartridge byte in the low two bits of each channel.
        rgba = image.convert("RGBA")
        original = bytes((a & 3) << 6 | (r & 3) << 4 | (g & 3) << 2 | b & 3
                         for r, g, b, a in rgba.getdata())[:0x8000]
    player = replace_once(engine.read_text(), "var _cartname=[`trichromat.p8`]",
                          "var _cartname=[`main.p8`]")
    match = re.search(r"var _cartdat\s*=\s*\[([0-9,\s]*)\]", player)
    if match is None:
        raise ValueError("Missing cartridge array")
    # The original exporter wraps its decimal byte array every 256 bytes.
    lines = [",".join(map(str, original[i:i+256])) for i in range(0, len(original), 256)]
    export = player[:match.start(1)] + "\n" + ",\n".join(lines) + player[match.end(1):]
    if hashlib.sha256(export.encode()).hexdigest() != "dd2d5d6c35c0177e2893c71c8a922b3a9162cf14c0ec8ecd93efe2c1eb28baae":
        raise ValueError("Reconstructed Petal Quest 1.2 export differs from the benchmark source")
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        (work / "cart.rom").write_bytes(original)
        codec(work, "cart.rom", "cart.p8", "-F", "rom", "-f", "p8")
        source = (work / "cart.p8").read_text()
        start = source.index("__lua__\n") + len("__lua__\n")
        end = source.index("\n__gfx__", start)
        lua = source[start:end]
        for edit in json.loads((HERE / "lua-edits.json").read_text()):
            lua = replace_once(lua, edit["old"], edit["new"])
        (work / "cart.p8").write_text(source[:start] + lua + source[end:])
        codec(work, "cart.p8", "patched.rom", "-f", "rom")
        patched = (work / "patched.rom").read_bytes()
    if len(patched) != 0x8000 or patched[:0x4300] != original[:0x4300]:
        raise ValueError("Cartridge assets changed")
    player = player[:match.start(1)] + ",".join(map(str, patched)) + player[match.end(1):]
    # Match the reference's token ceiling, required by the appended GPIO code.
    player = replace_once(player, "if((o|0)>8192){Ig(51389,6);c[i>>2]=o;c[i+4>>2]=8192;",
                          "if((o|0)>65535){Ig(51389,6);c[i>>2]=o;c[i+4>>2]=65535;")
    path.write_text(player)
    engine.unlink()
    cart.unlink()
    html = (root / "index.html").read_text()
    html = replace_once(html, "</head>", (HERE / "layout.html").read_text() + "</head>")
    html = replace_once(html, "</body></html>",
                        '<div id="quest-ledger" aria-live="polite"></div><script src="game_api.js"></script>\n</body></html>')
    (root / "index.html").write_text(html)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch.py DOWNLOADED_GAME_DIRECTORY")
    main(Path(sys.argv[1]).resolve())
