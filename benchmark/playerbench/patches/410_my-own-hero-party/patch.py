"""Patch two GDScript resources using GDRETools 2.7.4 and system patch."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent


def main():
    root = Path(sys.argv[1]).resolve()
    pck = root / 'index.pck'
    if hashlib.sha256(pck.read_bytes()).hexdigest() != 'c2b57713a1d0b1152db29df5f97818c43db2e7ac5f6251756489fbcb3fbd2064':
        raise ValueError('Unexpected upstream PCK version')
    names = json.loads((HERE / 'scripts.json').read_text())
    with tempfile.TemporaryDirectory(prefix='hero-party-patch-', dir=root.parent) as tmp:
        work = Path(tmp)
        configured = os.environ.get('GAMEENV_GDRE') or shutil.which('gdre_tools.x86_64')
        if configured:
            gdre = Path(configured).resolve()
        else:
            vendor = HERE.parents[3] / 'tools/godot/vendor/GDRE_tools-v2.7.4-linux.zip'
            if not vendor.is_file():
                raise RuntimeError('Install GDRETools 2.7.4 and set GAMEENV_GDRE to its executable')
            if hashlib.sha256(vendor.read_bytes()).hexdigest() != '3dc4f1244dda62e41e0c952ac00a69e60e07f3b0d5a211164806bf9d41a54758':
                raise ValueError('Unexpected GDRETools distribution')
            with zipfile.ZipFile(vendor) as archive:
                archive.extractall(work / 'gdre')
            gdre = work / 'gdre/gdre_tools.x86_64'
            gdre.chmod(0o755)
        env = dict(os.environ, XDG_DATA_HOME=str(work / 'xdg'),
                   XDG_CONFIG_HOME=str(work / 'config'), XDG_CACHE_HOME=str(work / 'cache'))

        def run(*args):
            result = subprocess.run([str(gdre), '--headless', *args], cwd=work, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=180)
            if result.returncode:
                raise RuntimeError('GDRE failed: ' + result.stdout[-3000:])

        extracted, source, compiled = (work / n for n in ('extracted', 'source', 'compiled'))
        run(f'--extract={pck}', *[f'--include=res://{n}' for n in names], f'--output={extracted}')
        run(*[f'--decompile={extracted / n}' for n in names], '--bytecode=4.7.0', f'--output={source}')
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(HERE / 'scripts.patch')],
                                cwd=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if result.returncode:
            raise RuntimeError('Script patch failed: ' + result.stdout[-3000:])
        run(*[f'--compile={source / Path(n).with_suffix(".gd").name}' for n in names],
            '--bytecode=4.7.0', f'--output={compiled}')
        output = work / 'patched.pck'
        run(f'--pck-patch={pck}', *[f'--patch-file={compiled / Path(n).name}=res://{n}' for n in names],
            f'--output={output}')
        shutil.copyfile(output, pck)

    entry = root / 'index.html'
    html = entry.read_text()
    anchor = '<script src="index.js"></script>'
    if html.count(anchor) != 1:
        raise ValueError('Unexpected upstream engine tag')
    html = html.replace(anchor, '<script src="game_api.js"></script>\n\t\t' + anchor)
    external = '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>'
    if html.count(external) != 1:
        raise ValueError('Unexpected itch embed script')
    html = html.replace(external, '')
    html, count = re.subn(r'"index.pck":\d+', f'"index.pck":{pck.stat().st_size}', html)
    if count != 1:
        raise ValueError('Unexpected upstream PCK size declaration')
    entry.write_text(html)


if __name__ == '__main__':
    main()
