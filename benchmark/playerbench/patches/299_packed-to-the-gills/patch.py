"""Reproduce the reference's three entry-page adapter edits; leave builds untouched."""
from pathlib import Path
import sys


def main():
    entry = Path(sys.argv[1]) / 'index.html'
    text = entry.read_text()
    edits = [
        ('<script>navigator.serviceWorker&&(navigator.serviceWorker.controller||navigator.serviceWorker.register("./service-worker.js",{updateViaCache:"all"}))</script>', ''),
        ('<script src=renpy-pre.js></script>', '<script src=renpy-pre.js></script><script src="game_api.js"></script>'),
        ('<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', ''),
    ]
    for before, after in edits:
        if text.count(before) != 1:
            raise ValueError('Original entry-page anchor changed')
        text = text.replace(before, after, 1)
    entry.write_text(text)


if __name__ == '__main__':
    main()
