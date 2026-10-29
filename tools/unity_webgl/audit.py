"""Summarize reviewed Unity profiles without launching their games."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .profile import DEFAULT_PROFILE_DIR, load_profile


def _address_kinds(value: object) -> Counter[str]:
    kinds: Counter[str] = Counter()
    if isinstance(value, dict):
        kind = value.get("kind")
        if kind in {"absolute", "static_fields", "method", "chain"}:
            kinds[str(kind)] += 1
        for child in value.values():
            kinds.update(_address_kinds(child))
    elif isinstance(value, list):
        for child in value:
            kinds.update(_address_kinds(child))
    return kinds


def audit_profile(path: Path | str) -> dict[str, Any]:
    """Return a compact structural audit for one validated profile."""
    source = Path(path).resolve()
    profile = load_profile(source)
    fields = profile["state"]["fields"]
    source_kinds = Counter(str(value["kind"]) for value in fields.values())
    address_kinds = _address_kinds(fields)
    constant_only = set(source_kinds) == {"constant"}
    return {
        "game_id": profile["game_id"],
        "profile": str(source),
        "field_count": len(fields),
        "source_kinds": dict(sorted(source_kinds.items())),
        "address_kinds": dict(sorted(address_kinds.items())),
        "constant_only": constant_only,
        "uses_absolute_addresses": bool(address_kinds["absolute"]),
        "allows_method_calls": bool(profile["runtime"].get("allow_method_calls")),
    }


def audit_profiles(directory: Path | str = DEFAULT_PROFILE_DIR) -> dict[str, Any]:
    """Audit every YAML profile in a directory and aggregate recurring traits."""
    root = Path(directory).resolve()
    profiles = [audit_profile(path) for path in sorted(root.glob("*.yaml"))]
    return {
        "summary": {
            "profiles": len(profiles),
            "fields": sum(item["field_count"] for item in profiles),
            "constant_only_profiles": sum(item["constant_only"] for item in profiles),
            "absolute_address_profiles": sum(
                item["uses_absolute_addresses"] for item in profiles
            ),
            "method_call_profiles": sum(item["allows_method_calls"] for item in profiles),
        },
        "profiles": profiles,
    }


__all__ = ["audit_profile", "audit_profiles"]
