"""Locate Unity WebGL build artifacts without modifying a game directory."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_URL_LITERAL_RE = re.compile(r"[\"']([^\"']+)[\"']")
_LEGACY_JSON_RE = re.compile(r"[\"']([^\"']+\.json(?:\?[^\"']*)?)[\"']", re.I)
_EXCLUDED_PARTS = {"_external", "node_modules", ".git"}


class DiscoveryError(RuntimeError):
    """Raised when a folder does not contain one unambiguous Unity build."""


@dataclass(frozen=True, slots=True)
class UnityBuild:
    """Paths that make up one Unity WebGL player build."""

    root: Path
    index_html: Path
    loader: Path
    data: Path
    framework: Path
    wasm: Path
    legacy_config: Path | None = None

    @property
    def game_id(self) -> str:
        return self.root.name

    def relative(self, path: Path | None) -> str | None:
        if path is None:
            return None
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def files(self) -> tuple[Path, ...]:
        values = (self.index_html, self.loader, self.data, self.framework, self.wasm)
        return values + ((self.legacy_config,) if self.legacy_config else ())

    def fingerprint(self) -> str:
        """Hash build inputs using bounded reads, independent of absolute paths."""
        digest = hashlib.sha256()
        for path in sorted(self.files(), key=lambda item: self.relative(item) or ""):
            relative = self.relative(path)
            digest.update((relative or "").encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.stat().st_size.to_bytes(8, "little"))
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def as_dict(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "game_id": self.game_id,
            "root": str(self.root.resolve()),
            "index_html": self.relative(self.index_html),
            "loader": self.relative(self.loader),
            "data": self.relative(self.data),
            "framework": self.relative(self.framework),
            "wasm": self.relative(self.wasm),
            "legacy_config": self.relative(self.legacy_config),
        }
        if include_fingerprint:
            result["fingerprint"] = self.fingerprint()
        return result


def _is_allowed(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return not any(part in _EXCLUDED_PARTS for part in relative.parts)


def _files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file() and _is_allowed(path, root)]


def _text(path: Path, *, limit: int = 2_000_000) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _referenced_paths(index: Path, root: Path) -> set[Path]:
    text = _text(index)
    references: set[Path] = set()
    for raw in _URL_LITERAL_RE.findall(text):
        # This is a conservative hint extractor, not a JavaScript parser.  A
        # minified expression can span quote styles and look like one enormous
        # "literal"; never turn that into a filesystem operation.
        if len(raw) > 1024 or "\n" in raw or "\r" in raw:
            continue
        clean = raw.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
        if not clean or clean.startswith(("http://", "https://", "//", "data:")):
            continue
        candidate = (index.parent / clean).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            references.add(candidate)
    return references


def _legacy_config(index: Path, root: Path) -> Path | None:
    for raw in _LEGACY_JSON_RE.findall(_text(index)):
        candidate = (index.parent / raw.split("?", 1)[0]).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        keys = {str(key).lower() for key in payload} if isinstance(payload, dict) else set()
        if keys & {"dataurl", "wasmcodeurl", "codeurl", "asmcodeurl"}:
            return candidate
    return None


def _legacy_references(config: Path | None, root: Path) -> set[Path]:
    if config is None:
        return set()
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    references: set[Path] = set()
    for value in payload.values():
        if not isinstance(value, str):
            continue
        candidate = (config.parent / value.split("?", 1)[0]).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            references.add(candidate)
    return references


def _pick(
    role: str,
    candidates: Iterable[Path],
    *,
    root: Path,
    references: set[Path],
) -> Path:
    candidates = list(dict.fromkeys(path.resolve() for path in candidates))
    if not candidates:
        raise DiscoveryError(f"Unity {role} artifact was not found under {root}")

    def score(path: Path) -> tuple[int, int, int, str]:
        relative = path.relative_to(root.resolve())
        lower_parts = [part.lower() for part in relative.parts]
        return (
            1 if path in references else 0,
            1 if "build" in lower_parts else 0,
            -len(relative.parts),
            relative.as_posix(),
        )

    ranked = sorted(candidates, key=score, reverse=True)
    if len(ranked) > 1 and score(ranked[0])[:3] == score(ranked[1])[:3]:
        names = ", ".join(path.relative_to(root.resolve()).as_posix() for path in ranked[:4])
        raise DiscoveryError(f"Ambiguous Unity {role} artifacts under {root}: {names}")
    return ranked[0]


def _choose_index(root: Path, files: list[Path]) -> Path:
    preferred = root / "index.html"
    if preferred.is_file():
        return preferred.resolve()
    html = [path for path in files if path.suffix.lower() in {".html", ".htm"}]
    unity_html = [
        path
        for path in html
        if any(token in _text(path).lower() for token in ("createunityinstance", "unityloader"))
    ]
    return _pick("HTML entry point", unity_html or html, root=root, references=set())


def discover_build(game_dir: Path | str) -> UnityBuild:
    """Discover one modern or legacy Unity WebGL build in ``game_dir``."""
    root = Path(game_dir).resolve()
    if not root.is_dir():
        raise DiscoveryError(f"Game directory does not exist: {root}")
    files = _files(root)
    index = _choose_index(root, files)
    legacy = _legacy_config(index, root)
    references = _referenced_paths(index, root) | _legacy_references(legacy, root)

    lower = {path: path.name.lower() for path in files}
    loader_candidates = [path for path, name in lower.items() if name.endswith(".loader.js")]
    if not loader_candidates:
        loader_candidates = [
            path for path, name in lower.items() if name == "unityloader.js" or "unityloader" in name
        ]
    data_candidates = [
        path
        for path, name in lower.items()
        if re.search(r"\.data(?:\.(?:gz|br|unityweb))?$", name)
        or name.endswith(".data.unityweb")
    ]
    wasm_candidates = [
        path
        for path, name in lower.items()
        if re.search(r"\.wasm(?:\.(?:gz|br|unityweb))?$", name)
        or name.endswith(".wasm.unityweb")
        or name.endswith(".wasm.code.unityweb")
    ]
    framework_candidates = [
        path
        for path, name in lower.items()
        if ".framework.js" in name
        or name.endswith("framework.js.unityweb")
        or name.endswith(".wasm.framework.unityweb")
    ]

    return UnityBuild(
        root=root,
        index_html=index,
        loader=_pick("loader", loader_candidates, root=root, references=references),
        data=_pick("data", data_candidates, root=root, references=references),
        framework=_pick("framework", framework_candidates, root=root, references=references),
        wasm=_pick("WASM", wasm_candidates, root=root, references=references),
        legacy_config=legacy,
    )


__all__ = ["DiscoveryError", "UnityBuild", "discover_build"]
