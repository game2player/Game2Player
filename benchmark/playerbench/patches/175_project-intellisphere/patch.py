"""Rebuild nine patched GDScript resources, preserving the downloaded PCK assets.

Dependency: GDRETools 2.7.4 (framework vendor ZIP, PATH, or GAMEENV_GDRE),
and the system `patch` utility. No game resources are bundled with this adapter.
"""
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
    names = json.loads((HERE / 'scripts.json').read_text())
    pck = root / 'intellisphere.pck'
    expected = '6379ef959696dad8c1ed8e21751d0520ff72485bc1912a3b820505c64b42bbfb'
    if hashlib.sha256(pck.read_bytes()).hexdigest() != expected:
        raise ValueError('Unexpected upstream PCK version')
    with tempfile.TemporaryDirectory(prefix='intellisphere-patch-', dir=root.parent) as tmp:
        work = Path(tmp)
        configured = os.environ.get('GAMEENV_GDRE') or shutil.which('gdre_tools.x86_64')
        if configured:
            gdre = Path(configured).resolve()
        else:
            # The framework ships this unchanged upstream dependency.
            vendor = HERE.parents[3] / 'tools/godot/vendor/GDRE_tools-v2.7.4-linux.zip'
            if not vendor.is_file():
                raise RuntimeError('Install GDRETools 2.7.4 and set GAMEENV_GDRE to its executable')
            if hashlib.sha256(vendor.read_bytes()).hexdigest() != '3dc4f1244dda62e41e0c952ac00a69e60e07f3b0d5a211164806bf9d41a54758':
                raise ValueError('Unexpected GDRETools vendor distribution')
            with zipfile.ZipFile(vendor) as archive:
                archive.extractall(work / 'gdre')
            gdre = work / 'gdre/gdre_tools.x86_64'
            gdre.chmod(0o755)
        env = dict(os.environ, XDG_DATA_HOME=str(work / 'xdg'), XDG_CONFIG_HOME=str(work / 'config'),
                   XDG_CACHE_HOME=str(work / 'cache'))
        def run(*args):
            result = subprocess.run([str(gdre), '--headless', *args], cwd=work,
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=180)
            if result.returncode:
                raise RuntimeError('GDRE failed: ' + result.stdout[-3000:])
        extracted = work / 'extracted'
        source = work / 'source'
        compiled = work / 'compiled'
        run(f'--extract={pck}', *[f'--include=res://{n}' for n in names], f'--output={extracted}')
        run(*[f'--decompile={extracted / n}' for n in names], '--bytecode=3.4.2', f'--output={source}')
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(HERE / 'scripts.patch')],
                       cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        run(*[f'--compile={source / Path(n).with_suffix(".gd").name}' for n in names],
            '--bytecode=3.4.2', f'--output={compiled}')
        output = work / 'intellisphere.pck'
        run(f'--pck-patch={pck}', *[f'--patch-file={compiled / Path(n).name}=res://{n}' for n in names],
            f'--output={output}')
        shutil.copyfile(output, pck)
    entry = root / 'index.html'
    html = entry.read_text()
    anchor = "\t<script type='text/javascript' src='intellisphere.js'></script>"
    if html.count(anchor) != 1:
        raise ValueError('Unexpected upstream engine script tag')
    html = html.replace(anchor, "\t<script src='game_api.js'></script>\n" + anchor)
    guard = '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>'
    if html.count(guard) != 1:
        raise ValueError('Unexpected upstream itch host script')
    html = html.replace(guard, '')
    html, count = re.subn(r'"intellisphere.pck":\d+', f'"intellisphere.pck":{pck.stat().st_size}', html)
    if count != 1:
        raise ValueError('Unexpected upstream PCK size declaration')
    entry.write_text(html)


if __name__ == '__main__':
    main()
