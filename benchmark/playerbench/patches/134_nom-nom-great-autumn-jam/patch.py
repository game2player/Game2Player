"""Install the existing adapter hooks in the pinned upstream HTML entrypoint."""
from pathlib import Path
import sys


def patch(root: Path) -> None:
    entry = root / "index.html"
    text = entry.read_text(encoding="utf-8")
    hosting_script = '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>'
    title = "\t<title>NOMNOM - Great Autumn Jam</title>\n"
    if text.count(hosting_script) != 1 or text.count(title) != 1:
        raise ValueError("Unexpected upstream HTML: expected one hosting script and title")
    text = text.replace(hosting_script, "")
    text = text.replace(title, title + '\t<script src="render_patch.js"></script>\n\t<script src="game_api.js"></script>\n')
    entry.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch.py GAME_DIRECTORY")
    patch(Path(sys.argv[1]))
