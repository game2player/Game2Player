"""Instrument the original cart; preserve the official player and all ROM assets.

Requires shrinko8 v1.2.7b, importable as shrinko.shrinko8.
"""
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main(root):
    player = root / "dc14.js"
    source = player.read_text()
    if hashlib.sha256(player.read_bytes()).hexdigest() != "d5a01a622c4d308a4da37aa38427164df6ab7423ce533576744a44bef8750039":
        raise ValueError("Expected the original Dusk Child 1.4 player")
    match = re.search(r"var _cartdat\s*=\s*\[([0-9,\s]*)\]", source)
    if match is None:
        raise ValueError("Original cartridge array missing")
    original = bytes(int(v) for v in match[1].split(",") if v.strip())
    if len(original) != 0x8000 or original[0x4300:0x4304] != b":c:\0":
        raise ValueError("Unexpected cartridge format")
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        rom, cart, rebuilt = temp / "original.rom", temp / "cart.p8", temp / "patched.rom"
        rom.write_bytes(original)
        subprocess.run([sys.executable, "-m", "shrinko.shrinko8", str(rom), str(cart), "-f", "p8"], check=True)
        text = cart.read_text()
        start, end = "px=24--360--player x", 'textscroll=7\n\n--update game object stuff'
        if text.count(start) != 1 or text.count(end) != 1 or text.count("__gfx__") != 1:
            raise ValueError("Original code anchors changed")
        text = text.replace(start, "--gw: initial globals wrapped so a reset can restore them\nfunction gw_vars()\n" + start)
        text = text.replace(end, 'textscroll=7\nend\ngw_vars()\n\n--update game object stuff')
        injection = Path(__file__).with_name("instrumentation.lua").read_text()
        text = text.replace("__gfx__", injection + "__gfx__")
        cart.write_text(text)
        subprocess.run([sys.executable, "-m", "shrinko.shrinko8", str(cart), str(rebuilt), "-f", "rom", "--old-compression"], check=True)
        patched = rebuilt.read_bytes()
    if len(patched) != len(original) or patched[:0x4300] != original[:0x4300]:
        raise ValueError("Repacking changed original cartridge assets")
    player.write_text(source[:match.start(1)] + ",".join(map(str, patched)) + source[match.end(1):])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch.py GAME_DIRECTORY")
    main(Path(sys.argv[1]))
