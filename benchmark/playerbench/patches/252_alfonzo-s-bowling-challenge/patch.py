"""Patch decoded Lua only; keep the downloaded PICO-8 engine and assets."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
import tempfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def convert(work, *args):
    subprocess.run([sys.executable, "-m", "shrinko.shrinko8", *args],
                   cwd=work, check=True, capture_output=True, text=True)


def main(root):
    package = Path(__file__).resolve().parent
    script = root / "abc.js"
    raw = script.read_bytes()
    assert sha(raw) == "3f6a5a4a6f9fc62485cafbd3225219c60e21dfbaf1896f0e348f7d6c6d1e8f13"
    text = raw.decode("utf-8")
    match = re.search(r"var _cartdat\s*=\s*\[([0-9,\s]+)\]", text)
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
        assert sha(lua.encode()) == edits["input_sha256"]
        for edit in reversed(edits["edits"]):
            lua = lua[:edit["start"]] + edit["text"] + lua[edit["end"]:]
        assert sha(lua.encode()) == edits["output_sha256"]
        (work / "cart.p8").write_text(source[:start] + lua + "\n" + source[end:], encoding="utf-8")
        convert(work, "cart.p8", "patched.rom", "-f", "rom")
        rebuilt = (work / "patched.rom").read_bytes()
    assert len(rebuilt) == len(rom)
    assert rebuilt[:0x4300] == rom[:0x4300], "Original assets must stay byte-identical"
    text = text[:match.start(1)] + ",".join(str(x) for x in rebuilt) + text[match.end(1):]
    # Match the reference's sole engine edit, allowing the instrumentation tokens.
    anchor = "if((o|0)>8192){sg(47660,6);c[i>>2]=o;c[i+4>>2]=8192;"
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor.replace("8192", "65535"))
    script.write_text(text, encoding="utf-8")

    html = root / "index.html"
    raw = html.read_bytes()
    assert sha(raw) == "ee9613de61bb283d4c0ce47d40ea571c41c94e7a33a8010db07a9ce978c99db2"
    text = raw.decode("utf-8-sig")
    text = text.replace("<title>PICO-8 Cartridge</title>", """<!-- Alfonzo's Bowling Challenge by Andrew "kittenm4ster" Anderson.
Source: https://kittenm4ster.itch.io/abc
The author lists code under MIT and assets under CC BY-NC-SA 4.0.
Local changes: offline shell, task starts, seeded RNG and semantic GPIO hooks. -->

<title>Alfonzo's Bowling Challenge</title>""", 1)
    text = text.replace("</head>", '<link rel="stylesheet" href="adapter.css"></head>', 1)
    text = text.replace("</body>", '<script src="game_api.js"></script>\n</body>', 1)
    text = re.sub(r'\n<script defer src="https://static\.itch\.io/htmlgame\.js" type="text/javascript"></script>\s*$', "", text)
    html.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
