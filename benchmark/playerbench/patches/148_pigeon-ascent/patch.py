"""Reproduce the existing adapter using GDRETools; retain upstream PCK assets."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def gdre_binary(package, work):
    # Optional installed GDRETools, or the framework's unmodified 2.7.4 ZIP.
    configured = os.environ.get("GAMEENV_GDRE") or shutil.which("gdre_tools.x86_64")
    if configured:
        return Path(configured).resolve()
    archive = package.parents[3] / "tools/godot/vendor/GDRE_tools-v2.7.4-linux.zip"
    if archive.is_file():
        with zipfile.ZipFile(archive) as source:
            source.extractall(work / "gdre")
        binary = work / "gdre/gdre_tools.x86_64"
        binary.chmod(0o755)
        return binary
    raise RuntimeError("Install GDRETools 2.7.4 and set GAMEENV_GDRE, or provide the framework's tools/godot/vendor ZIP")


def main(root):
    package = Path(__file__).resolve().parent
    pck = root / "index.pck"
    assert digest(pck.read_bytes()) == "e3762dc454c865395a59811f96ebe446dc673dbfda5fe3e9b811c3511db3089e"
    edits = json.loads((package / "script-edits.json").read_text())
    with tempfile.TemporaryDirectory(prefix="pigeon-patch-") as temp:
        work = Path(temp)
        binary = gdre_binary(package, work)
        environment = dict(os.environ, XDG_DATA_HOME=str(work / "data"), XDG_CONFIG_HOME=str(work / "config"), XDG_CACHE_HOME=str(work / "cache"))

        def gdre(*args):
            result = subprocess.run([str(binary), "--headless", *args], cwd=work,
                                    env=environment, capture_output=True, text=True, timeout=300)
            if result.returncode:
                raise RuntimeError("GDRETools failed: " + (result.stdout + result.stderr)[-2000:])

        gdre(f"--extract={pck}", "--include=res://scenes/global.gd", f"--output={work / 'extract'}")
        script = work / "extract/scenes/global.gd"
        raw = script.read_bytes()
        assert digest(raw) == edits["input_sha256"]
        source = raw.decode("utf-8")
        for edit in reversed(edits["edits"]):
            source = source[:edit["start"]] + edit["text"] + source[edit["end"]:]
        raw = source.encode("utf-8")
        assert digest(raw) == edits["output_sha256"]
        script.write_bytes(raw)
        gdre(f"--pck-patch={pck}", f"--patch-file={script}=res://scenes/global.gd", f"--output={work / 'patched.pck'}")
        shutil.copyfile(work / "patched.pck", pck)

    html = root / "index.html"
    source = html.read_bytes().decode("utf-8")
    anchor = '<script src="index.js"></script>'
    assert source.count(anchor) == 1
    source = source.replace(anchor, '<script src="game_api.js"></script>\n\t\t' + anchor)
    size = '"index.pck":21185008'
    assert source.count(size) == 1
    source = source.replace(size, f'"index.pck":{pck.stat().st_size}')
    external = '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>'
    assert source.count(external) == 1
    source = source.replace(external, "")
    html.write_bytes(source.encode("utf-8"))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
