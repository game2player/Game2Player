"""Prepare and run static IL2CPP analysis in the selected cache."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .archive import prepare_il2cpp_inputs
from .cache import build_cache_dir, default_cache_dir
from .discovery import UnityBuild, discover_build
from .symbols import build_symbol_index
from tools.engine_tools import tool_path


class AnalysisError(RuntimeError):
    """Raised when a configured analysis backend fails."""


@dataclass(frozen=True, slots=True)
class PreparedBuild:
    build: UnityBuild
    fingerprint: str
    cache_dir: Path
    wasm: Path
    metadata: Path
    metadata_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "build": self.build.as_dict(include_fingerprint=False),
            "fingerprint": self.fingerprint,
            "cache_dir": str(self.cache_dir),
            "wasm": str(self.wasm),
            "metadata": str(self.metadata),
            "metadata_version": self.metadata_version,
        }


def metadata_version(path: Path) -> int:
    with path.open("rb") as handle:
        raw = handle.read(8)
    if len(raw) != 8:
        raise AnalysisError(f"Truncated IL2CPP metadata: {path}")
    sanity, version = struct.unpack("<II", raw)
    if sanity != 0xFAB11BAF:
        raise AnalysisError(f"Invalid IL2CPP metadata header: {path}")
    return version


def prepare_build(game_dir: Path | str, cache_dir: Path | None = None) -> PreparedBuild:
    build = discover_build(game_dir)
    fingerprint = build.fingerprint()
    root_cache = cache_dir or default_cache_dir()
    target = build_cache_dir(fingerprint, root_cache)
    wasm, metadata = prepare_il2cpp_inputs(build.data, build.wasm, target)
    prepared = PreparedBuild(
        build=build,
        fingerprint=fingerprint,
        cache_dir=target,
        wasm=wasm,
        metadata=metadata,
        metadata_version=metadata_version(metadata),
    )
    target.mkdir(parents=True, exist_ok=True)
    manifest = target / "build.json"
    temporary = manifest.with_name(f".{manifest.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(prepared.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(manifest)
    return prepared


# Il2CppDumper is vendored in the repository but its .NET runtime is not: it is
# platform-specific. Say where to get one rather than just reporting it
# missing, because the answer differs per environment and is otherwise unwritten.
_DOTNET_HELP = """A .NET 6.0 runtime is required by the bundled Il2CppDumper DLL (not the SDK).
Use dotnet on PATH, install the runtime into ~/.local/share/game2player/runtimes/dotnet,
or select its executable with GAMEENV_DOTNET or --dotnet /path/to/dotnet.
See README.md for installation. A newer major runtime alone is not sufficient."""


def _dotnet_command(dumper: Path, dotnet: Path | None) -> list[str]:
    if dumper.suffix.lower() == ".dll":
        try:
            runtime = dotnet or tool_path("dotnet")
        except OSError as exc:
            raise AnalysisError(_DOTNET_HELP) from exc
        if not runtime.is_file():
            raise AnalysisError(_DOTNET_HELP)
        return [str(runtime), str(dumper)]
    if not os.access(dumper, os.X_OK):
        raise AnalysisError(f"Il2CppDumper executable is not executable: {dumper}")
    return [str(dumper)]


def _stage_il2cppdumper(dumper: Path, cache_root: Path) -> Path:
    """Create a non-interactive, low-output copy without mutating the tool."""
    source_config = dumper.parent / "config.json"
    try:
        config = json.loads(source_config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"Unable to read Il2CppDumper config beside {dumper}: {exc}") from exc
    if not isinstance(config, dict):
        raise AnalysisError(f"Invalid Il2CppDumper config beside {dumper}")
    config.update(
        {
            "RequireAnyKey": False,
            "GenerateDummyDll": False,
            # Il2CppDumper emits script.json (including TypeInfo addresses)
            # as part of its "struct" generation phase.
            "GenerateStruct": True,
            "GenerateScript": True,
            "DumpMethod": True,
            "DumpField": True,
            "DumpFieldOffset": True,
            "DumpMethodOffset": True,
            "DumpTypeDefIndex": True,
        }
    )
    digest = hashlib.sha256(dumper.read_bytes())
    digest.update(json.dumps(config, sort_keys=True).encode("utf-8"))
    stage = cache_root / "tools" / "il2cppdumper" / digest.hexdigest()[:16]
    staged_dumper = stage / dumper.name
    staged_config = stage / "config.json"
    if staged_dumper.is_file() and staged_config.is_file():
        return staged_dumper
    stage.mkdir(parents=True, exist_ok=True)
    for source in dumper.parent.iterdir():
        if not source.is_file() or source.name == "config.json":
            continue
        if source.suffix.lower() not in {".dll", ".json", ".exe"}:
            continue
        shutil.copy2(source, stage / source.name)
    staged_config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if not staged_dumper.is_file():
        raise AnalysisError(f"Failed to stage Il2CppDumper from {dumper}")
    return staged_dumper


def analyze_with_il2cppdumper(
    prepared: PreparedBuild,
    dumper: Path | str,
    *,
    dotnet: Path | str | None = None,
    force: bool = False,
    timeout_s: int = 1800,
) -> Path:
    """Run Il2CppDumper and build a queryable symbol database."""
    dumper_path = Path(dumper).expanduser().resolve()
    if not dumper_path.is_file():
        raise AnalysisError(f"Il2CppDumper was not found: {dumper_path}")
    dotnet_path = Path(dotnet).expanduser().resolve() if dotnet else None
    cache_root = prepared.cache_dir.parents[1]
    staged_dumper = _stage_il2cppdumper(dumper_path, cache_root)
    output = prepared.cache_dir / "il2cppdumper"
    database = output / "symbols.sqlite3"
    if database.is_file() and not force:
        return database
    if output.exists() and (force or not database.is_file()):
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    command = _dotnet_command(staged_dumper, dotnet_path)
    command.extend([str(prepared.wasm), str(prepared.metadata), str(output)])
    try:
        completed = subprocess.run(
            command,
            cwd=staged_dumper.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AnalysisError(f"Il2CppDumper failed to execute: {exc}") from exc
    log = output / "analysis.log"
    log.write_text(completed.stdout or "", encoding="utf-8", errors="replace")
    if completed.returncode != 0 or not (output / "script.json").is_file():
        tail = "\n".join((completed.stdout or "").splitlines()[-12:])
        raise AnalysisError(
            f"Il2CppDumper failed for metadata v{prepared.metadata_version} "
            f"(exit {completed.returncode}); see {log}\n{tail}"
        )
    return build_symbol_index(output, database)


__all__ = [
    "AnalysisError",
    "PreparedBuild",
    "analyze_with_il2cppdumper",
    "metadata_version",
    "prepare_build",
]
