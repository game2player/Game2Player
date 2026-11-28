"""Apply the reference PlayerBench page integration to the original HTML export."""
from pathlib import Path
import sys


def main():
    entry = Path(sys.argv[1]) / "index.html"
    text = entry.read_text(encoding="utf-8")
    edits = [
        ("<title>Created with GameMaker Studio 2</title>", "<title>Sulka</title>"),
        ("background: #0;", "background: #0e081a;"),
        ('width="768" height="768"', 'width="576" height="576" tabindex="0"'),
        ('        <script>window.onload = GameMaker_Init;</script>',
         '        <script src="game_api.js"></script>\n'
         '        <script>window.onload = GameMaker_Init;</script>\n'
         '        <div id="level-info" aria-live="polite" style="position:fixed;top:10px;left:0;width:100%;text-align:center;pointer-events:none;color:#92c644;font:14px monospace;visibility:hidden"></div>'),
        ('<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', ""),
    ]
    for before, after in edits:
        if text.count(before) != 1:
            raise ValueError(f"Expected exactly one original HTML anchor: {before!r}")
        text = text.replace(before, after, 1)
    entry.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
