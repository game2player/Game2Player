"""Insert independently compiled AS2 adapter actions; retain upstream bytecode.

patch-data/*.bin are only the accompanying authored *.as, compiled in an empty
five-frame SWF by JPEXS FFDec 26.3.0. They contain no upstream game code/assets.
Only SWF tag framing and zlib compression are handled here (stdlib only).
"""
from pathlib import Path
import hashlib
import struct
import sys
import zlib

ORIGINAL_SHA256 = 'c8ce18026a386a05cd81fa202cabd67c66fc723e45321ded9e25588c6919a5aa'
DATA = Path(__file__).with_name('patch-data')


def tag(code, payload):
    size = len(payload)
    return (struct.pack('<H', code << 6 | size) if size < 63 else
            struct.pack('<HI', code << 6 | 63, size)) + payload


def actions(name):
    return tag(12, (DATA / (name + '.bin')).read_bytes())


def rewrite(stream, *, sprite=False):
    pos = 0
    frame = 1
    output = bytearray()
    found = set()
    while pos < len(stream):
        start = pos
        header, = struct.unpack_from('<H', stream, pos)
        pos += 2
        code, size = header >> 6, header & 63
        if size == 63:
            size, = struct.unpack_from('<I', stream, pos)
            pos += 4
        payload = stream[pos:pos+size]
        if len(payload) != size:
            raise ValueError('Truncated SWF tag')
        pos += size
        raw = stream[start:pos]
        if code == 12:
            if sprite and frame == 1:
                raw += actions('live')
                found.add('live')
            elif not sprite and frame in (3, 4):
                name = {3: 'loader', 4: 'play'}[frame]
                raw = actions(name)
                found.add(name)
            elif not sprite and frame in (6, 21):
                name = {6: 'bridge', 21: 'router'}[frame]
                raw += actions(name)
                found.add(name)
        elif code == 39 and not sprite and struct.unpack_from('<H', payload)[0] == 345:
            patched, nested = rewrite(payload[4:], sprite=True)
            if nested != {'live'}:
                raise ValueError('Expected sprite 345 first-frame action')
            raw = tag(39, payload[:4] + patched)
            found.update(nested)
        output.extend(raw)
        if code == 1:
            frame += 1
    return bytes(output), found


def main(directory):
    path = Path(directory) / 'game.swf'
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256:
        raise ValueError('Wrong upstream SWF version')
    if original[:3] != b'CWS':
        raise ValueError('Expected CWS SWF')
    body = zlib.decompress(original[8:])
    start = (5 + 4 * (body[0] >> 3) + 7) // 8 + 4
    patched, found = rewrite(body[start:])
    if found != {'loader', 'play', 'bridge', 'live', 'router'}:
        raise ValueError('Expected five adapter insertion points')
    body = body[:start] + patched
    path.write_bytes(original[:4] + struct.pack('<I', len(body) + 8) + zlib.compress(body, 9))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: patch.py GAME_DIRECTORY')
    main(sys.argv[1])
