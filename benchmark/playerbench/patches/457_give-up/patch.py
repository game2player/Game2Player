"""Reconstruct the AS3 bridge tag using upstream ranges and new compiled bytes.

This only reads SWF framing and zlib; it does not implement an AS3 engine.
All other tags, including every asset, are copied from the pinned original.
"""
from pathlib import Path
import hashlib
import json
import struct
import sys
import zlib

ORIGINAL = '671e724ab7b2a9774b45944f8a303925605804e775adcc2d8e4d3adbbab187e4'
RESULT = '15afc60d5d9a2c368e6ac96395e7e2dcf6c780282430a51df97343e26a5fe75b'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch(directory):
    path = directory / 'game.swf'
    source = path.read_bytes()
    if sha(source) != ORIGINAL or source[:3] != b'CWS':
        raise ValueError('Expected the pinned original Give Up SWF')
    movie = b'FWS' + source[3:8] + zlib.decompress(source[8:])
    if len(movie) != struct.unpack_from('<I', movie, 4)[0]:
        raise ValueError('Invalid original SWF length')
    data = Path(__file__).parent / 'patch-data'
    recipes = {r['tag_index']: r for r in json.loads((data / 'abc-ranges.json').read_text())}
    inserts = (data / 'abc-insert.bin').read_bytes()
    position = 8 + (5 + 4 * (movie[8] >> 3) + 7) // 8 + 4
    output = bytearray(movie[:position])
    index = 0
    changed = set()
    while position < len(movie):
        start = position
        header, = struct.unpack_from('<H', movie, position)
        position += 2
        size = header & 63
        if size == 63:
            size, = struct.unpack_from('<I', movie, position)
            position += 4
        end = position + size
        if end > len(movie):
            raise ValueError('Truncated original SWF tag')
        if index in recipes:
            recipe = recipes[index]
            original = movie[position:end]
            if header >> 6 != 82 or sha(original) != recipe['original_sha256']:
                raise ValueError('Unexpected original ActionScript tag')
            pieces = []
            for kind, offset, length in recipe['operations']:
                if kind not in ('copy', 'insert'):
                    raise ValueError('Unknown patch operation')
                buffer = original if kind == 'copy' else inserts
                if offset < 0 or length < 0 or offset + length > len(buffer):
                    raise ValueError('Patch range out of bounds')
                pieces.append(buffer[offset:offset + length])
            replacement = b''.join(pieces)
            if len(replacement) != recipe['result_length'] or sha(replacement) != recipe['result_sha256']:
                raise ValueError('Reconstructed ActionScript tag differs')
            output.extend(struct.pack('<HI', (82 << 6) | 63, len(replacement)))
            output.extend(replacement)
            changed.add(index)
        else:
            output.extend(movie[start:end])
        index += 1
        position = end
    if changed != set(recipes):
        raise ValueError('Missing ActionScript insertion points')
    output[4:8] = struct.pack('<I', len(output))
    if sha(output) != RESULT:
        raise ValueError('Reconstructed movie differs from the inspected adapter')
    path.write_bytes(b'CWS' + output[3:8] + zlib.compress(output[8:], 9))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: patch.py GAME_DIRECTORY')
    patch(Path(sys.argv[1]))
