"""Per-game browser probes used only by scripted policies."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .workspace import policy_workspace


PROBES_DIR = Path(__file__).with_name("probes")


def probe_path(game_id: str) -> Path:
    stem = str(game_id).strip().replace("-", "_")
    workspace = policy_workspace()
    if workspace is not None:
        return workspace / "probes" / f"g{stem}.js"
    return PROBES_DIR / f"g{stem}.js"


@dataclass(frozen=True, slots=True)
class PolicyProbe:
    path: Path
    source: str
    sha256: str


def load_probe(game_id: str) -> PolicyProbe:
    path = probe_path(game_id)
    try:
        source = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ValueError(f"No policy probe for {game_id}: {path}") from exc
    if not source:
        raise ValueError(f"Policy probe is empty: {path}")
    if "__policyProbe" not in source:
        raise ValueError(
            f"Policy probe must install window.__policyProbe: {path}"
        )
    return PolicyProbe(
        path=path,
        source=source,
        sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )


__all__ = ["PROBES_DIR", "PolicyProbe", "load_probe", "probe_path"]
