"""Reproduce the historical benchmark edits using the author's original text."""
import hashlib
import json
from pathlib import Path
import sys


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    root = Path(sys.argv[1])
    patch = json.loads(Path(__file__).with_name("bundle-patch.json").read_text())
    source = root / "assets/index-zM3THtlj.js"
    original = source.read_bytes()
    if sha256(original) != patch["input_sha256"]:
        raise ValueError("Unexpected original JavaScript version")
    text = original.decode("utf-8")
    pieces = []
    for operation in patch["operations"]:
        if isinstance(operation, str):
            pieces.append(operation)
        else:
            offset, length = operation
            if offset < 0 or length <= 0 or offset + length > len(text):
                raise ValueError("Invalid original text range")
            pieces.append(text[offset:offset + length])
    result = "".join(pieces).encode("utf-8")
    if sha256(result) != patch["output_sha256"]:
        raise ValueError("Reconstructed benchmark JavaScript differs")
    (root / "assets/index-Bsr45iMv.js").write_bytes(result)
    source.unlink()
    entry = root / "index.html"
    html = entry.read_text()
    anchor = '    <script type="module" crossorigin src="/assets/index-zM3THtlj.js"></script>'
    if html.count(anchor) != 1:
        raise ValueError("Unexpected upstream HTML entry")
    html = html.replace(anchor, '    <script src="/game_api.js"></script>\n'
                        '    <script type="module" crossorigin src="/assets/index-Bsr45iMv.js"></script>')
    entry.write_text(html)


if __name__ == "__main__":
    main()
