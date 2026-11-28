"""Adapt the original jam build's HTML; preserve all native runtime files."""
from pathlib import Path
import sys


def main():
    root = Path(sys.argv[1])
    path = root / "index.html"
    text = path.read_text(encoding="utf-8")
    edits = [
        ("<title>Volunteer</title>", "<title>Forward (Game Jam version)</title>"),
        ('<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', ""),
        ("<script src=js/chunk-vendors.c5cc457a.js></script>",
         '<script src="game_api.js"></script><script src=js/chunk-vendors.c5cc457a.js></script>'),
    ]
    for before, after in edits:
        if text.count(before) != 1:
            raise ValueError(f"Expected exactly one HTML anchor: {before!r}")
        text = text.replace(before, after, 1)
    path.write_text(text.rstrip("\n"), encoding="utf-8")


if __name__ == "__main__":
    main()
