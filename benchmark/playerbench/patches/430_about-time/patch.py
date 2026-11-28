"""Rebuild adapter-only GDScript edits using the downloaded GDRETools 2.7.4."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main(root):
    root = root.resolve()
    package = Path(__file__).resolve().parent
    changes = json.loads((package / 'script-edits.json').read_text())
    pack = root / 'AboutTime.pck'
    if digest(pack.read_bytes()) != '503fa13fe78ac737481b00693763f2353ffcd1a824c315841853eb5d7811b8b0':
        raise ValueError('Unexpected upstream PCK version')
    tool_dir = root / '_patch_tools'
    tool = tool_dir / 'gdre_tools.x86_64'
    tool.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix='about-time-patch-', dir=root.parent) as temporary:
        work = Path(temporary)
        env = dict(os.environ, XDG_DATA_HOME=str(work / 'xdg'),
                   XDG_CONFIG_HOME=str(work / 'config'), XDG_CACHE_HOME=str(work / 'cache'))

        def gdre(*args):
            result = subprocess.run([str(tool), '--headless', *map(str, args)],
                                    cwd=work, env=env, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError('GDRE failed: ' + (result.stdout + result.stderr)[-4000:])

        extracted, decoded, compiled = (work / n for n in ['extracted', 'decoded', 'compiled'])
        gdre(f'--extract={pack}', f'--output={extracted}',
             *(f"--include=res://{c['path']}" for c in changes))
        gdre(*(f"--decompile={extracted / c['path']}" for c in changes),
             '--bytecode=4.7.0', f'--output={decoded}')
        patched_files = []
        for change in changes:
            path = change['path']
            source = decoded / Path(path).with_suffix('.gd').name
            text = source.read_text()
            if digest(text.encode()) != change['source_sha256']:
                raise ValueError('Unexpected decompiled source: ' + path)
            lines = text.splitlines(True)
            for edit in reversed(change['edits']):
                lines[edit['start']:edit['end']] = [edit['text']]
            text = ''.join(lines)
            if digest(text.encode()) != change['patched_source_sha256']:
                raise ValueError('Source patch mismatch: ' + path)
            source.write_text(text)
            patched_files.append(f'--patch-file={compiled / Path(path).name}=res://{path}')
        gdre(*(f"--compile={decoded / Path(c['path']).with_suffix('.gd').name}" for c in changes),
             '--bytecode=4.7.0', f'--output={compiled}')
        output = work / 'patched.pck'
        gdre(f'--pck-patch={pack}', f'--output={output}', *patched_files)
        if not output.is_file():
            raise RuntimeError('GDRE did not produce the patched PCK')
        shutil.copyfile(output, pack)
    shutil.rmtree(tool_dir)

    html = root / 'index.html'
    raw = html.read_bytes()
    if digest(raw) != '5b468cd3bfe69cd3517faa0289b2f9a818faf5a07a21e684934df5b92d4a4f41':
        raise ValueError('Unexpected upstream HTML version')
    text = raw.decode()
    anchor = '\t\t<script src="AboutTime.js"></script>'
    if text.count(anchor) != 1:
        raise ValueError('Missing unique engine script tag')
    text = text.replace(anchor, '\t\t<script src="game_api.js"></script>\n' + anchor)
    integration = '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>'
    if text.count(integration) != 1:
        raise ValueError('Missing unique itch integration tag')
    html.write_text(text.replace(integration, ''))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
