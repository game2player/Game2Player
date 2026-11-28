"""Apply the small, existing PlayerBench changes to the pinned Construct export."""
import json
from pathlib import Path
import sys


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Expected one patch anchor: {old!r}')
    return text.replace(old, new, 1)


def main(root):
    path = root / 'index.html'
    html = path.read_text()
    html = replace_once(html, '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', '')
    html = replace_once(html, '<script src="scripts/modernjscheck.js">',
                        '<script src="game_api.js"></script>\n\t<script src="scripts/modernjscheck.js">')
    path.write_text(html)

    path = root / 'scripts/main.js'
    script = replace_once(path.read_text(), 'const e=true;window["c3_runtimeInterface"]',
                          'const e=false;window["c3_runtimeInterface"]')
    path.write_text(script)

    path = root / 'data.json'
    data = json.loads(path.read_text())
    removed = 0
    for layout in data['project'][5]:
        for layer in layout[9]:
            instances = layer[14]
            kept = [instance for instance in instances if instance[1] not in (71, 72)]
            removed += len(instances) - len(kept)
            layer[14] = kept
    if removed != 2:
        raise ValueError(f'Expected two ad instances, found {removed}')
    previous = [v for v in data['project'][6][0][1] if v[:2] == [1, 'PrevLevel']]
    if len(previous) != 1 or previous[0][3] != 1:
        raise ValueError('Unexpected PrevLevel definition')
    previous[0][3] = 0
    path.write_text(json.dumps(data, separators=(',', ':')))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
