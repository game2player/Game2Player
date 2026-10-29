"""Resolve policy artifacts from an optional experiment workspace."""

from __future__ import annotations

import os
from pathlib import Path


POLICY_WORKSPACE_ENV = "GAMEENV_POLICY_WORKSPACE"


def policy_workspace() -> Path | None:
    value = os.environ.get(POLICY_WORKSPACE_ENV, "").strip()
    return Path(value).expanduser().resolve() if value else None


__all__ = ["POLICY_WORKSPACE_ENV", "policy_workspace"]
