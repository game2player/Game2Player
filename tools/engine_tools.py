"""Locate shared engine analyzers; installation is an explicit setup step."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

from tools.paths import cache_dir


_VENDOR = Path(__file__).resolve().parent
DISTRIBUTIONS = {
    "ffdec": _VENDOR / "flash/vendor/ffdec_26.3.0.zip",
    "gdre": _VENDOR / "godot/vendor/GDRE_tools-v2.7.4-linux.zip",
}


def tools_dir() -> Path:
    return Path(os.environ.get("GAMEENV_ENGINE_TOOLS", cache_dir("engine_tools"))).expanduser().resolve()


def tool_path(name: str, *, unpack: bool = True) -> Path:
    configured = os.environ.get(f"GAMEENV_{name.upper()}")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"GAMEENV_{name.upper()} is not a file: {path}")
        return path
    distribution = DISTRIBUTIONS.get(name)
    if distribution is not None:
        if not distribution.is_file():
            raise FileNotFoundError(f"Bundled {name} distribution is missing: {distribution}")
        relative = {"ffdec": "ffdec/ffdec.jar", "gdre": "gdre/gdre_tools.x86_64"}
        path = tools_dir() / relative[name]
        if not path.is_file():
            if not unpack:
                return path
            # Builds running side by side may unpack the same tool at once. Extract
            # privately and publish with one rename, so no process ever runs a
            # half-written binary; a losing rename means another process published it.
            tools_dir().mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=tools_dir()))
            staging.chmod(0o755)
            with zipfile.ZipFile(distribution) as archive:
                archive.extractall(staging)
            if name == "gdre":
                binary = staging / path.name
                binary.chmod(binary.stat().st_mode | 0o111)
            try:
                staging.rename(tools_dir() / name)
            except OSError:
                shutil.rmtree(staging, ignore_errors=True)
    else:
        found = shutil.which(name)
        if found:
            return Path(found)
        project_bin = Path(sys.executable).parent / name
        if project_bin.is_file():
            return project_bin
        relative = {"java": "java/bin/java", "dotnet": "dotnet/dotnet"}
        path = Path.home() / ".local/share/game2player/runtimes" / relative[name]
    if not path.is_file():
        raise FileNotFoundError(f"{name} is not available; expected {path}. See tools/engine_inspect/README.md.")
    return path


def run_backend(name: str, argv: list[str]) -> int:
    """Pass upstream arguments and exit status through without translating them."""
    try:
        backend = tool_path(name)
        if name == "ffdec":
            command = ([str(tool_path("java")), "-Djava.awt.headless=true", "-jar", str(backend)]
                       if backend.suffix.lower() == ".jar" else [str(backend)])
            args = argv or ["-help"]
        else:
            command = [str(backend), "--headless"]
            args = argv or ["--help"]
        return subprocess.run(command + args, stdin=subprocess.DEVNULL, check=False).returncode
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"{name}: {exc}", file=sys.stderr)
        return 2
