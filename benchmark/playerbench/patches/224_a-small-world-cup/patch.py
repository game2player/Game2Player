"""Install the external PlayerBench adapter in the pinned Construct entrypoint."""
from pathlib import Path
import sys


def patch(root: Path) -> None:
    entry = root / 'index.html'
    content = entry.read_bytes()
    runtime = b'<script src="c2runtime.js"></script>'
    itch_host = b'<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>'
    if content.count(runtime) != 1 or content.count(itch_host) != 1:
        raise ValueError('Unexpected original HTML: adapter anchors do not match')
    content = content.replace(runtime, runtime + b'\r\n    <script src="game_api.js"></script>', 1)
    # This hosting integration is not a game dependency and contacts itch.io.
    content = content.replace(itch_host, b'', 1)
    entry.write_bytes(content)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: patch.py GAME_DIRECTORY')
    patch(Path(sys.argv[1]))
