"""Reproduce the existing adapter in one decompressed SWF DoABC section.

bridge.bsdiff is BSDIFF40 data, generated with bsdiff4 1.4.0 after inspecting
FFDec 26.3.0 exports. It contains compiled edits, not a compressed SWF diff.
The standard BSDIFF40 reader below requires only Python's standard library.
"""
from pathlib import Path
import bz2
import hashlib
import struct
import sys
import zlib


def require_hash(data, expected):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Unexpected source version or patch result")


def number(data):
    # BSDIFF40 integers use little-endian sign-magnitude encoding.
    value = int.from_bytes(data, "little")
    return -(value & ((1 << 63) - 1)) if value >> 63 else value


def apply_delta(original, patch):
    if patch[:8] != b"BSDIFF40":
        raise ValueError("Invalid BSDIFF40 header")
    control_size, diff_size, output_size = [number(patch[p:p+8]) for p in (8, 16, 24)]
    if min(control_size, diff_size, output_size) < 0:
        raise ValueError("Invalid patch sizes")
    control = bz2.decompress(patch[32:32+control_size])
    delta = bz2.decompress(patch[32+control_size:32+control_size+diff_size])
    extra = bz2.decompress(patch[32+control_size+diff_size:])
    old_pos = diff_pos = extra_pos = 0
    output = bytearray()
    for pos in range(0, len(control), 24):
        add, insert, seek = [number(control[p:p+8]) for p in range(pos, pos+24, 8)]
        if min(add, insert) < 0 or len(output) + add + insert > output_size:
            raise ValueError("Invalid patch operation")
        for i in range(add):
            value = original[old_pos+i] if 0 <= old_pos+i < len(original) else 0
            output.append((value + delta[diff_pos+i]) & 255)
        output.extend(extra[extra_pos:extra_pos+insert])
        old_pos += add + seek
        diff_pos += add
        extra_pos += insert
    if len(output) != output_size or diff_pos != len(delta) or extra_pos != len(extra):
        raise ValueError("Incomplete patch")
    return bytes(output)


def main(directory):
    path = Path(directory) / "game.swf"
    compressed = path.read_bytes()
    require_hash(compressed, "028ecf43b4bd40399823e0478d6c62bba935c7d826ad6e1a195ebf43cf474767")
    if compressed[:3] != b"CWS":
        raise ValueError("Expected zlib-compressed SWF")
    swf = compressed[:8] + zlib.decompress(compressed[8:])
    # Skip the variable-width SWF RECT, frame rate and frame count.
    pos = 8 + (5 + 4 * (swf[8] >> 3) + 7) // 8 + 4
    count = 0
    while pos < len(swf):
        start = pos
        header, = struct.unpack_from("<H", swf, pos)
        pos += 2
        code, size = header >> 6, header & 63
        if size == 63:
            size, = struct.unpack_from("<I", swf, pos)
            pos += 4
        if count == 41:
            if code != 82:
                raise ValueError("Expected DoABC tag 41")
            abc_start = swf.index(b"\0", pos+4) + 1
            abc = swf[abc_start:pos+size]
            require_hash(abc, "291090d80b6cce014f9d731a54976c74d27c422f46ec2165f412c1535ad12c4d")
            patched = apply_delta(abc, Path(__file__).with_name("bridge.bsdiff").read_bytes())
            require_hash(patched, "a2f1752181ddfd8696ad38d5a5bd09e7767b8c9bfa43484a81a121a30c81b216")
            payload = swf[pos:abc_start] + patched
            swf = swf[:start] + struct.pack("<HI", (82 << 6) | 63, len(payload)) + payload + swf[pos+size:]
            swf = swf[:4] + struct.pack("<I", len(swf)) + swf[8:]
            require_hash(swf, "d48b8492211fcc8054325bfd2c902f01d51236846402f33656909442bcd4efb6")
            path.write_bytes(swf[:8] + zlib.compress(swf[8:], 9))
            return
        pos += size
        count += 1
    raise ValueError("Missing DoABC tag")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch.py GAME_DIRECTORY")
    main(sys.argv[1])
