"""Apply the historical localhost referrer edit and generate AAC fallback audio.

All audio is read from the user's downloaded originals; no game assets ship here.
Requires UnityPy 1.25.4 and ffmpeg with libvorbis.
"""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import warnings

import UnityPy
from UnityPy.helpers.ResourceReader import get_resource_data


def patch(root: Path) -> None:
    framework = root / 'Build/Rhythm Quest Demo.framework.js'
    old = 'function _GetReferrer(){var result=document.referrer;'
    new = ('function _GetReferrer(){var result=(location.hostname === "127.0.0.1" || '
           'location.hostname === "localhost") ? "https://ddrkirbyisq.itch.io/rhythm-quest" '
           ': document.referrer;')
    source = framework.read_text()
    if source.count(old) != 1:
        raise ValueError('Expected original 0.43.0 referrer function exactly once')
    framework.write_text(source.replace(old, new, 1))

    ffmpeg = shutil.which('ffmpeg') or str(Path(sys.executable).parent / 'ffmpeg')
    UnityPy.config.FALLBACK_UNITY_VERSION = '6000.2.6f2'
    warnings.filterwarnings('ignore', module='UnityPy')
    audio_dir = root / 'audio'
    audio_dir.mkdir(exist_ok=True)
    mapping = {}
    inputs = [root / 'Build/Rhythm Quest Demo.data']
    inputs += sorted((root / 'StreamingAssets/aa/WebGL').glob('*.bundle'))
    with tempfile.TemporaryDirectory(prefix='rhythm-quest-audio-') as temp:
        aac = Path(temp) / 'input.m4a'
        for path in inputs:
            env = UnityPy.load(str(path))
            for obj in env.objects:
                if obj.type.name != 'AudioClip':
                    continue
                clip = obj.read()
                resource = clip.m_Resource
                data = (bytes(clip.m_AudioData) if clip.m_AudioData else
                        get_resource_data(resource.m_Source, obj.assets_file,
                                          resource.m_Offset, resource.m_Size)
                        if resource else b'')
                if data[4:8] != b'ftyp':
                    continue
                digest = hashlib.sha256(data).hexdigest()
                relative = 'audio/' + digest + '.ogg'
                if digest not in mapping:
                    aac.write_bytes(data)
                    subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(aac),
                                    '-c:a', 'libvorbis', '-q:a', '5', '-fflags', '+bitexact',
                                    str(root / relative)],
                                   check=True)
                mapping[digest] = relative
            del env
    if len(mapping) != 197:
        raise ValueError(f'Expected 197 original AAC clips, found {len(mapping)}')
    (root / 'audio_manifest.json').write_text(json.dumps(mapping, sort_keys=True) + '\n')
    print(f'Generated {len(mapping)} local Ogg fallbacks; patched one referrer function.', flush=True)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: patch.py GAME_DIRECTORY')
    patch(Path(sys.argv[1]).resolve())
