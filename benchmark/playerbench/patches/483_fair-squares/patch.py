"""Rebuild only the 11 adapter-edited GDScript resources with GDRETools 2.7.4."""
from pathlib import Path
import hashlib
import json
import os
import re
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
    tool_dir = root / '_patch_tools'
    tool = tool_dir / 'gdre_tools.x86_64'
    tool.chmod(0o755)
    pack = root / 'index.pck'
    assert digest(pack.read_bytes()) == 'b953275c17576d673f8e1465df80b0f29335394520ebda40814b0f2735efd184'
    with tempfile.TemporaryDirectory(prefix='fair-squares-patch-', dir=root) as temporary:
        work = Path(temporary)
        env = dict(os.environ, XDG_DATA_HOME=str(work / 'data'),
                   XDG_CONFIG_HOME=str(work / 'config'), XDG_CACHE_HOME=str(work / 'cache'))

        def gdre(*args):
            result = subprocess.run([str(tool), '--headless', *map(str, args)], cwd=work,
                                    env=env, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError('GDRE failed: ' + (result.stdout + result.stderr)[-3000:])

        extracted = work / 'extracted'
        gdre(f'--extract={pack}', f'--output={extracted}',
             *(f"--include=res://{c['path']}" for c in changes))
        compiled = work / 'compiled'
        patched_files = []
        for i, change in enumerate(changes):
            path = change['path']
            decoded = work / 'decoded' / str(i)
            gdre(f'--decompile={extracted / path}', '--bytecode=3.5.1', f'--output={decoded}')
            source = decoded / Path(path).with_suffix('.gd').name
            text = source.read_text()
            assert digest(text.encode()) == change['source_sha256'], path
            lines = text.splitlines(True)
            for edit in reversed(change['edits']):
                lines[edit['start']:edit['end']] = [edit['text']]
            text = ''.join(lines)
            assert digest(text.encode()) == change['patched_source_sha256'], path
            source.write_text(text)
            gdre(f'--compile={source}', '--bytecode=3.5.1', f'--output={compiled}')
            result = compiled / Path(path).name
            assert result.is_file(), path
            patched_files.append(f'--patch-file={result}=res://{path}')
        output = work / 'patched.pck'
        gdre(f'--pck-patch={pack}', f'--output={output}', *patched_files)
        output.replace(pack)
    shutil.rmtree(tool_dir)

    html = root / 'index.html'
    raw = html.read_bytes()
    assert digest(raw) == '96a654841af138ccd4328f0625bfc8d3dbdb42142f4cddce8aa294c9f39c64d6'
    text = raw.decode()
    text, count = re.subn(r'<script defer src="https://static.itch.io/htmlgame.js"[^>]*></script>', '', text)
    assert count == 1
    text, count = re.subn(r"<link[^>]*(?:index.icon.png|index.apple-touch-icon.png)[^>]*>", '', text)
    assert count == 2
    anchor = "<script type='text/javascript' src='index.js'></script>"
    assert text.count(anchor) == 1
    text = text.replace(anchor, "<div id='game-hud' style='position:fixed;left:16px;top:10px;color:#fcef8d;font:18px monospace;pointer-events:none;z-index:10;white-space:pre-line'></div>\n\t<script src='game_api.js'></script>\n\t" + anchor)
    text, count = re.subn(r'"index.pck":\d+', f'"index.pck":{pack.stat().st_size}', text)
    assert count == 1
    html.write_text(text)


if __name__ == '__main__':
    main(Path(sys.argv[1]))
