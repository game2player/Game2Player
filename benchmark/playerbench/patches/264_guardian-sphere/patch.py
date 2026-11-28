"""Apply adapter-only source edits with the pinned upstream GDRE compiler."""
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
    changes = json.loads((package / "script-edits.json").read_text())
    tool_dir = root / "_patch_tools"
    tool = tool_dir / "gdre_tools.x86_64"
    tool.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix="guardian-patch-", dir=root) as temporary:
        work = Path(temporary)
        env = dict(os.environ, XDG_DATA_HOME=str(work / "userdata"))

        def gdre(*args):
            result = subprocess.run([str(tool), "--headless", *map(str, args)],
                                    cwd=work, env=env, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError("GDRE failed: " + (result.stdout + result.stderr)[-4000:])

        pack = root / "index.pck"
        assert digest(pack.read_bytes()) == "68a96340dfb609ebac7afc5a2274ab11f242dca6fc12a2d06536048306ccae0e"
        extracted = work / "extracted"
        gdre(f"--extract={pack}", f"--output={extracted}",
             *(f"--include=res://{c['path']}" for c in changes))
        compiled = work / "compiled"
        patched_files = []
        for change in changes:
            path = change["path"]
            decoded = work / (Path(path).stem + "-decoded")
            gdre(f"--decompile={extracted / path}", "--bytecode=3.2.2", f"--output={decoded}")
            source = decoded / Path(path).with_suffix(".gd").name
            text = source.read_text()
            assert digest(text.encode()) == change["source_sha256"], path
            lines = text.splitlines(True)
            for edit in reversed(change["edits"]):
                lines[edit["start"]:edit["end"]] = [edit["text"]]
            text = "".join(lines)
            assert digest(text.encode()) == change["patched_source_sha256"], path
            source.write_text(text)
            gdre(f"--compile={source}", "--bytecode=3.2.2", f"--output={compiled}")
            result = compiled / Path(path).name
            assert result.is_file(), path
            patched_files.append(f"--patch-file={result}=res://{path}")
        output = work / "patched.pck"
        gdre(f"--pck-patch={pack}", f"--output={output}", *patched_files)
        assert output.is_file()
        output.replace(pack)
    shutil.rmtree(tool_dir)

    html = root / "index.html"
    raw = html.read_bytes()
    assert digest(raw) == "9a7016fae54b1fccdeded39f41b60dde734cc9cc5806261fd9e160cf16abdc49"
    text = raw.decode()
    anchor = "\t<script type='text/javascript' src='index.js'></script>"
    assert text.count(anchor) == 1
    text = text.replace(anchor, "\t<script src='game_api.js'></script>\n" + anchor)
    integration = '\n<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>'
    assert text.endswith(integration)
    html.write_text(text[:-len(integration)])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
