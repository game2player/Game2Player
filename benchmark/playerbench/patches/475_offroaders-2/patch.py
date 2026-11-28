"""Apply small code deltas to two decompressed SWF DoABC tags.

BSDIFF40 payloads reference original bytecode; they contain no game assets.
Only SWF tag framing, zlib and the standard BSDIFF40 format are handled here.
"""
from pathlib import Path
import bz2
import hashlib
import json
import struct
import sys
import zlib

ORIGINAL = "faf4ef0d8ba948a0d56a685761881c9053ea7b7dea2d24ad4d25a3a108cc4df6"
RESULT = "bc54541b52a901854ecf757fad9e32dd74d970e00753f2ef55f0f599a3879849"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def integer(data):
    if len(data) != 8:
        raise ValueError("Truncated BSDIFF40 integer")
    value = int.from_bytes(data[:7] + bytes([data[7] & 127]), "little")
    return -value if data[7] & 128 else value


def apply_delta(original, delta, expected_length):
    if delta[:8] != b"BSDIFF40":
        raise ValueError("Expected BSDIFF40")
    control_length, diff_length, size = [integer(delta[i:i+8]) for i in (8, 16, 24)]
    if min(control_length, diff_length) < 0 or size != expected_length:
        raise ValueError("Invalid BSDIFF40 lengths")
    boundary = 32 + control_length
    control = bz2.decompress(delta[32:boundary])
    diff = bz2.decompress(delta[boundary:boundary+diff_length])
    extra = bz2.decompress(delta[boundary+diff_length:])
    output = bytearray()
    old = di = ei = 0
    if len(control) % 24:
        raise ValueError("Truncated BSDIFF40 controls")
    for pos in range(0, len(control), 24):
        take, insert, seek = [integer(control[j:j+8]) for j in range(pos, pos+24, 8)]
        if min(take, insert) < 0 or len(output)+take+insert > size:
            raise ValueError("Invalid BSDIFF40 output range")
        if di+take > len(diff) or ei+insert > len(extra):
            raise ValueError("Truncated BSDIFF40 payload")
        for i in range(take):
            value = original[old+i] if 0 <= old+i < len(original) else 0
            output.append((value + diff[di+i]) & 255)
        output.extend(extra[ei:ei+insert])
        old += take + seek
        di += take
        ei += insert
    if len(output) != size or di != len(diff) or ei != len(extra):
        raise ValueError("BSDIFF40 reconstruction size differs")
    return bytes(output)


def patch(directory):
    path = directory / "game.swf"
    source = path.read_bytes()
    if sha(source) != ORIGINAL or source[:3] != b"CWS":
        raise ValueError("Expected the pinned original Offroaders 2 SWF")
    movie = b"FWS" + source[3:8] + zlib.decompress(source[8:])
    if len(movie) != struct.unpack_from("<I", movie, 4)[0]:
        raise ValueError("Invalid original SWF length")
    data = Path(__file__).parent / "patch-data"
    recipes = {r["tag_index"]: r for r in json.loads((data / "tags.json").read_text())}
    position = 8 + (5 + 4 * (movie[8] >> 3) + 7) // 8 + 4
    output = bytearray(movie[:position])
    index = 0
    changed = set()
    while position < len(movie):
        start = position
        header, = struct.unpack_from("<H", movie, position)
        position += 2
        size = header & 63
        if size == 63:
            size, = struct.unpack_from("<I", movie, position)
            position += 4
        end = position + size
        if end > len(movie):
            raise ValueError("Truncated original SWF tag")
        if index in recipes:
            recipe = recipes[index]
            original = movie[position:end]
            if header >> 6 != 82 or sha(original) != recipe["original_sha256"]:
                raise ValueError("Unexpected original ActionScript tag")
            replacement = apply_delta(original, (data / recipe["patch"]).read_bytes(),
                                      recipe["result_length"])
            if sha(replacement) != recipe["result_sha256"]:
                raise ValueError("Reconstructed ActionScript tag differs")
            output.extend(struct.pack("<HI", (82 << 6) | 63, len(replacement)))
            output.extend(replacement)
            changed.add(index)
        else:
            output.extend(movie[start:end])
        index += 1
        position = end
    if changed != set(recipes):
        raise ValueError("Missing ActionScript insertion points")
    output[4:8] = struct.pack("<I", len(output))
    if sha(output) != RESULT:
        raise ValueError("Reconstructed movie differs from the inspected adapter")
    path.write_bytes(b"CWS" + output[3:8] + zlib.compress(output[8:], 9))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch.py GAME_DIRECTORY")
    patch(Path(sys.argv[1]))
