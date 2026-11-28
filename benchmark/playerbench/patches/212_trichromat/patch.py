"""Reproduce the adapter edits in decoded Lua, keeping all upstream assets."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def convert(work, *args):
    subprocess.run([sys.executable, "-m", "shrinko.shrinko8", *args],
                   cwd=work, check=True, capture_output=True, text=True)


def main(root):
    package = Path(__file__).resolve().parent
    script = root / "trichromat.js"
    raw = script.read_bytes()
    assert digest(raw) == "13a361d3c2eaeb3923d356298fd7edbdcd60172f98cc7b2ebf89b61c53259c67"
    text = raw.decode("utf-8")
    match = re.search(r"var _cartdat=\[([0-9,\s]+)\]", text)
    assert match
    rom = bytes(int(x) for x in match[1].split(",") if x.strip())
    assert len(rom) == 0x8000
    edits = json.loads((package / "lua-edits.json").read_text())
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        (work / "cart.rom").write_bytes(rom)
        convert(work, "cart.rom", "cart.p8", "-F", "rom", "-f", "p8")
        source = (work / "cart.p8").read_text(encoding="utf-8")
        start = source.index("__lua__\n") + len("__lua__\n")
        end = re.search(r"^__\w+__\s*$", source[start:], re.M).start() + start
        lua = "\n".join(source[start:end].splitlines())
        assert digest(lua.encode()) == edits["input_sha256"]
        for edit in reversed(edits["edits"]):
            lua = lua[:edit["start"]] + edit["text"] + lua[edit["end"]:]
        assert digest(lua.encode()) == edits["output_sha256"]
        (work / "cart.p8").write_text(source[:start] + lua + "\n" + source[end:], encoding="utf-8")
        convert(work, "cart.p8", "patched.rom", "-f", "rom")
        rebuilt = (work / "patched.rom").read_bytes()
    assert len(rebuilt) == len(rom)
    assert rebuilt[:0x4300] == rom[:0x4300], "Assets must remain unchanged"
    text = text[:match.start(1)] + ",".join(str(x) for x in rebuilt) + text[match.end(1):]
    script.write_text(text, encoding="utf-8")

    html = root / "index.html"
    raw = html.read_bytes()
    assert digest(raw) == "735afc4c287d5849824ce2ff5ea58fb0758072af0745ad01a7ded624b6103a26"
    text = raw.decode("utf-8")
    assert text.count("  </head>") == 1
    text = text.replace("  </head>", '  <style>#p8_menu_buttons,#p8_menu_buttons_touch{display:none !important}</style>\n<script src="game_api.js"></script>\n</head>', 1)
    integration = '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>'
    assert text.endswith(integration)
    text = text[:-len(integration)]
    html.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
