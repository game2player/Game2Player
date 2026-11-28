"""Convert the user's downloaded AAC audio to Opus, retaining all Unity offsets.

audio-ranges.json contains only resource names, byte ranges and durations read
from this build's 25 AudioClip records with UnityPy 1.25.4. No audio is bundled.
The UnityWebData directory and every byte outside those ranges stay unchanged.
"""
from pathlib import Path
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile


def patch(root):
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        raise RuntimeError('Install ffmpeg with the libopus encoder before installing this game')
    target = root / 'Build/adofaiii4.data.unityweb'
    data = bytearray(target.read_bytes())
    expected = '21fc89122fe8486bfd80adb284c3a9ec49488e1b3f4d85efc282e5eeb6cb2908'
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Expected the unmodified upstream web demo v0.4.2 data file')
    if data[:16] != b'UnityWebData1.0\0':
        raise ValueError('Unexpected UnityWebData container')
    end = struct.unpack_from('<I', data, 16)[0]
    pos, entries = 20, {}
    while pos < end:
        offset, size, length = struct.unpack_from('<III', data, pos)
        pos += 12
        name = data[pos:pos + length].decode('utf-8')
        pos += length
        if offset < end or offset + size > len(data):
            raise ValueError('Invalid resource range')
        entries[name] = (offset, size)
    clips = json.loads(Path(__file__).with_name('audio-ranges.json').read_text())
    with tempfile.TemporaryDirectory(prefix='adofai-audio-') as temporary:
        source, output = Path(temporary) / 'clip.m4a', Path(temporary) / 'clip.opus'
        for clip in clips:
            offset, size = entries[clip['src']]
            start, budget = offset + clip['off'], clip['size']
            if clip['off'] < 0 or clip['off'] + budget > size:
                raise ValueError('AudioClip is outside its resource')
            original = bytes(data[start:start + budget])
            if original[4:12] != b'ftypmp42':
                raise ValueError('Expected original AAC/MP4 audio')
            source.write_bytes(original)
            rate = min(192, int(budget * 8 * 0.8 / max(clip['len'], 0.05)) // 1000)
            for kbps in [rate, 128, 96, 64, 48, 32, 24]:
                subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(source),
                                '-c:a', 'libopus', '-b:a', f'{max(6, kbps)}k',
                                '-vbr', 'constrained', '-map_metadata', '-1',
                                '-fflags', '+bitexact', '-flags:a', '+bitexact',
                                str(output)], check=True)
                converted = output.read_bytes()
                if len(converted) <= budget:
                    break
            else:
                raise ValueError('Converted clip exceeds its original byte budget')
            if not converted.startswith(b'OggS'):
                raise ValueError('Expected Ogg Opus output')
            data[start:start + budget] = converted + bytes(budget - len(converted))
    target.write_bytes(data)
    config = root / 'Build/adofaiii4.json'
    text = config.read_text()
    line = '"wasmSymbolsUrl": "adofaiii4.wasm.symbols.unityweb",\n'
    if text.count(line) != 1:
        raise ValueError('Unexpected Unity build configuration')
    config.write_text(text.replace(line, ''))
    print(f'Converted {len(clips)} downloaded audio clips locally; Unity code and offsets preserved')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: patch.py DOWNLOADED_GAME_DIRECTORY')
    patch(Path(sys.argv[1]))
