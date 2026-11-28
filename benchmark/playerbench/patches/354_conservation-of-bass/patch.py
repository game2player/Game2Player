"""Apply the three entry-page edits used by the frozen PlayerBench environment."""
import sys
from pathlib import Path


def main():
    page = Path(sys.argv[1]) / "index.html"
    data = page.read_bytes().replace(b'\r\n', b'\n')
    edits = [
        (b'background: #0;', b'background: #000;'),
        (b'        <script>window.onload = GameMaker_Init;</script>',
         b'        <script src="game_api.js"></script>\n        <script>window.onload = GameMaker_Init;</script>'),
        (b'<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', b''),
    ]
    for before, after in edits:
        if data.count(before) != 1:
            raise ValueError("Unexpected original index.html: patch anchor must occur once")
        data = data.replace(before, after, 1)
    page.write_bytes(data)


if __name__ == "__main__":
    main()
