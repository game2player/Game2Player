"""Patch only decoded cartridge Lua, retaining the downloaded engine and assets."""
from pathlib import Path
import hashlib
import json
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
    script = root / "index.js"
    raw = script.read_bytes()
    assert digest(raw) == "388768fa8dfdd05c63a62e457127a3ab21933f53c509c3892a2b9482c00a6d65"
    text = raw.decode("utf-8")
    match = re.search(r"var _cartdat=\[([0-9,\s]+)\]", text)
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
        end = re.search(r"^__\w+__\s*$", source[start:], re.M).start() + start
        lua = "\n".join(source[start:end].splitlines())
        assert digest(lua.encode()) == edits["input_sha256"]
        for edit in reversed(edits["edits"]):
            lua = lua[:edit["start"]] + edit["text"] + lua[edit["end"]:]
        assert digest(lua.encode()) == edits["output_sha256"]
        (work / "cart.p8").write_text(source[:start] + lua + "\n" + source[end:], encoding="utf-8")
        shrinko(work, "cart.p8", "patched.rom", "-f", "rom")
        rebuilt = (work / "patched.rom").read_bytes()
    assert len(rebuilt) == len(rom)
    assert rebuilt[:0x4300] == rom[:0x4300], "Game assets must remain byte-identical"
    text = text[:match.start(1)] + ",".join(str(x) for x in rebuilt) + text[match.end(1):]
    script.write_text(text, encoding="utf-8")

    html = root / "index.html"
    text = html.read_text(encoding="utf-8-sig")
    text = text.replace("<html>", '<html data-game-id="89_celeste-classic-2">', 1)
    text = text.replace("var p8_autoplay = false;", "var p8_autoplay = true;", 1)
    text = text.replace("-->\n</STYLE>", "#p8_menu_buttons, #p8_menu_buttons_touch, #p8_start_button {\n\tdisplay: none !important;\n}\n\n-->\n</STYLE>", 1)
    text = text.replace("</head>", '<script defer src="game_api.js"></script>\n</head>', 1)
    text = text.replace("</body>", '\t<script src="game_api.js"></script>\n</body>', 1)
    # itch's host integration is unnecessary for the standalone offline export.
    text = re.sub(r'\n<script defer src="https://static\.itch\.io/htmlgame\.js" type="text/javascript"></script>\s*$', "", text)
    html.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
