"""Apply reference telemetry to decoded Lua, preserving upstream engine/assets."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def shrinko(work, *args):
    subprocess.run([sys.executable, "-m", "shrinko.shrinko8", *args],
                   cwd=work, check=True, capture_output=True, text=True)


def main(root):
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / "source.json").read_text())
    for item in manifest["downloads"]:
        assert digest((root / item["path"]).read_bytes()) == item["sha256"]
    script = root / "picopuzzleleague.js"
    text = script.read_text(encoding="utf-8")
    match = re.search(r"var _cartdat\s*=\s*\[([0-9,\s]+)\]", text)
    assert match
    rom = bytes(int(x) for x in match[1].split(",") if x.strip())
    assert len(rom) == 0x8000
    edits = json.loads((package / "lua-edits.json").read_text())
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        (work / "cart.rom").write_bytes(rom)
        shrinko(work, "cart.rom", "cart.p8", "-F", "rom", "-f", "p8")
        source = (work / "cart.p8").read_text(encoding="utf-8")
        start = source.index("__lua__\n") + len("__lua__\n")
        section = re.search(r"^__\w+__\s*$", source[start:], re.M)
        end = start + section.start() if section else len(source)
        lua = "\n".join(source[start:end].splitlines())
        assert digest(lua.encode()) == edits["input_sha256"]
        for edit in reversed(edits["edits"]):
            lua = lua[:edit["start"]] + edit["text"] + lua[edit["end"]:]
        assert digest(lua.encode()) == edits["output_sha256"]
        (work / "cart.p8").write_text(source[:start] + lua + "\n" + source[end:], encoding="utf-8")
        # The upstream PICO-8 0.1.12 player needs legacy compression.
        shrinko(work, "cart.p8", "patched.rom", "-f", "rom", "--old-compression")
        rebuilt = (work / "patched.rom").read_bytes()
    assert len(rebuilt) == len(rom)
    assert rebuilt[:0x4300] == rom[:0x4300], "Assets must remain byte-identical"
    script.write_text(text[:match.start(1)] + ",".join(str(x) for x in rebuilt) + text[match.end(1):], encoding="utf-8")

    html = root / "index.html"
    text = html.read_text(encoding="utf-8-sig")
    assert text.count("<html><head>") == 1
    text = text.replace("<html><head>", '<html><head><script src="game_api.js"></script>', 1)
    # Host integration is unnecessary for the standalone offline export.
    text = re.sub(r'<script defer src="https://static\.itch\.io/htmlgame\.js" type="text/javascript"></script>\s*$', "", text)
    html.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
