"""Attach the existing PlayerBench adapter to the pinned creator export."""
from pathlib import Path
import sys


def main():
    root = Path(sys.argv[1])
    entry = root / 'index.html'
    text = entry.read_text(encoding='utf-8')
    changes = [
        ('<title>Created with GameMaker</title>', '<title>Auto Necrochess</title>'),
        ('background: #0;', 'background: #1a1825;'),
        ('        </style>', '''        html, body { width:100%; height:100%; overflow:hidden; }
canvas { display:block; image-rendering:pixelated; }
#run-readout { position:absolute; top:139px; left:18px; right:18px; z-index:10; pointer-events:none; color:#f6ead8; background:rgba(26,24,37,.92); font:14px/22px monospace; padding:0 6px; border-radius:4px; }
</style>'''),
        ('<script>window.onload = GameMaker_Init;</script>',
         '<script>window.onload = GameMaker_Init;</script>\n'
         '        <div id="run-readout" hidden></div>\n'
         '        <script src="game_api.js"></script>'),
        ('<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', ''),
    ]
    for before, after in changes:
        if text.count(before) != 1:
            raise ValueError('Unexpected upstream entrypoint anchor: ' + before)
        text = text.replace(before, after, 1)
    entry.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
