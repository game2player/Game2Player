"""Insert the existing Build 40 bridge into the pinned original GWT payload."""
import ast
import hashlib
import json
from pathlib import Path
import sys


def main():
    root = Path(sys.argv[1])
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / 'source.json').read_text())
    names = ['index.html', 'html/6B7B6BB8AC29E8E3952F79D5483498AA.cache.js']
    for name in names:
        expected = next(item['sha256'] for item in manifest['downloads'] if item['path'] == name)
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Unexpected original: ' + name)

    bundle = root / names[1]
    encoded = bundle.read_text()
    prefix = 'html.onScriptDownloaded('
    if not encoded.startswith(prefix) or not encoded.rstrip().endswith(');'):
        raise ValueError('Unexpected GWT wrapper')
    # GWT emits an array of JS string literals. This permutation uses exactly
    # the literal syntax also accepted by Python; no game code is evaluated.
    chunks = ast.literal_eval(encoded[len(prefix):encoded.rindex(')')])
    if not isinstance(chunks, list) or not all(isinstance(c, str) for c in chunks):
        raise ValueError('Unexpected GWT chunks')
    code = ''.join(chunks)
    anchor = 'var gwtOnLoad=gwtOnLoad=omf;'
    if code.count(anchor) != 1:
        raise ValueError('Missing unique bridge insertion point')
    bridge = (package / 'native_bridge.js').read_text()
    code = code.replace(anchor, bridge + anchor)
    bundle.write_text(prefix + json.dumps([code], ensure_ascii=True) + ');\n')

    entry = root / 'index.html'
    html = entry.read_text()
    anchor = '<script type="text/javascript" src="html/html.nocache.js"></script>'
    if html.count(anchor) != 1:
        raise ValueError('Missing unique entry script')
    html = html.replace(anchor, '<script src="game_api.js"></script>\n              ' + anchor)
    # itch.io's host integration is unnecessary in an offline local game.
    html = html.replace('<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', '')
    entry.write_text(html)


if __name__ == '__main__':
    main()
