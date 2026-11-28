"""Rebuild the single changed AS3 tag using original ranges and bridge bytes."""
from pathlib import Path
import hashlib
import json
import struct
import sys
import zlib


ORIGINAL = "bfd95ef0c08b5d066c79479925a123200fcfa2a21add2e1f664602321b9ab6fd"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch(directory):
    path = directory / "game.swf"
    compressed = path.read_bytes()
    if sha(compressed) != ORIGINAL or compressed[:3] != b"CWS":
        raise ValueError("Expected the pinned original William and Sly SWF")
    movie = b"FWS" + compressed[3:8] + zlib.decompress(compressed[8:])
    if len(movie) != struct.unpack_from("<I", movie, 4)[0]:
        raise ValueError("Invalid uncompressed SWF length")
    data = Path(__file__).parent / "patch-data"
    recipe = json.loads((data / "abc-ranges.json").read_text())
    inserts = (data / "abc-insert.bin").read_bytes()
    # RECT, frame rate and frame count precede the root tag stream.
    position = 8 + (5 + 4 * (movie[8] >> 3) + 7) // 8 + 4
    for index in range(recipe["tag_index"] + 1):
        start = position
        header = struct.unpack_from("<H", movie, position)[0]
        position += 2
        size = header & 63
        if size == 63:
            size = struct.unpack_from("<I", movie, position)[0]
            position += 4
        end = position + size
        if end > len(movie):
            raise ValueError("Truncated SWF tag")
        if index == recipe["tag_index"]:
            if header >> 6 != 82:
                raise ValueError("Expected DoABC tag")
            original = movie[position:end]
            if sha(original) != recipe["original_sha256"]:
                raise ValueError("Original ActionScript tag differs")
            pieces = []
            for kind, offset, length in recipe["operations"]:
                if kind not in ("copy", "insert"):
                    raise ValueError("Unknown patch operation")
                source = original if kind == "copy" else inserts
                if offset < 0 or length < 0 or offset + length > len(source):
                    raise ValueError("Patch range out of bounds")
                pieces.append(source[offset:offset + length])
            replacement = b"".join(pieces)
            if len(replacement) != recipe["result_length"] or sha(replacement) != recipe["result_sha256"]:
                raise ValueError("Reconstructed ActionScript tag differs")
            tag = struct.pack("<HI", (82 << 6) | 63, len(replacement)) + replacement
            movie = movie[:start] + tag + movie[end:]
            break
        position = end
    movie = movie[:4] + struct.pack("<I", len(movie)) + movie[8:]
    path.write_bytes(b"CWS" + movie[3:8] + zlib.compress(movie[8:], 9))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch.py GAME_DIRECTORY")
    patch(Path(sys.argv[1]))
