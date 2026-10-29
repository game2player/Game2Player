"""Select a persistent coding-agent CLI without coupling workflows to it."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from tools.codex_session import (
    CodexProcessError,
    CodexProtocolError,
    CodexSession,
    CodexSessionError,
)


CodingProcessError = CodexProcessError
CodingProtocolError = CodexProtocolError
CodingSessionError = CodexSessionError


class CodingSession(Protocol):
    """The small session surface used by repository workflows."""

    thread_id: str | None
    turn_timeout_s: float
    environment: dict[str, str] | None

    def turn(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        sandbox: str = "workspace-write",
    ) -> dict[str, Any]: ...


AGENTS_DIR = Path(__file__).resolve().parents[1] / "catalog" / "agents"
BACKENDS = ("codex", "claude")
_AGENT_FIELDS = {"backend", "executable", "model", "reasoning_effort",
                 "credential_file", "service_tier"}


@dataclass(frozen=True)
class AgentProfile:
    """Which coding-agent CLI to run and with which model (catalog/agents/<name>.yaml)."""

    name: str
    backend: str
    executable: str
    model: str | None = None
    reasoning_effort: str = "high"
    # Claude only: the file holding the subscription token from `claude setup-token`.
    credential_file: Path | None = None
    # Codex only: the model's service tier, e.g. "priority" for Fast.
    service_tier: str | None = None

    def resolved_executable(self) -> Path:
        """The CLI to run. Codex is also looked up where its VS Code extension keeps it,
        or at $GAMEENV_CODEX_BIN; Claude
        Code where its installer or its VS Code extension puts it."""
        candidates = [self.executable]
        if self.backend == "codex":
            if os.environ.get("GAMEENV_CODEX_BIN"):
                candidates.insert(0, os.environ["GAMEENV_CODEX_BIN"])
            extensions = Path.home() / ".vscode-server" / "extensions"
            candidates += [str(path) for path in sorted(
                extensions.glob("openai.chatgpt-*/bin/linux-x86_64/codex"), reverse=True)]
        if self.backend == "claude":
            from tools.claude_session import claude_installs

            candidates += claude_installs()
        for candidate in candidates:
            path = Path(candidate).expanduser()
            if os.sep in str(candidate) and path.is_file() and os.access(path, os.X_OK):
                return path.resolve()
            found = shutil.which(candidate)
            if found:
                return Path(found).resolve()
        raise FileNotFoundError(f"{self.name}: the {self.backend} CLI was not found ({self.executable})")


def load_agent(ref: str) -> AgentProfile:
    """A coding-agent profile by catalog name, or from a YAML file path."""
    text = str(ref).strip()
    path = Path(text).expanduser() if text.endswith((".yaml", ".yml")) else AGENTS_DIR / f"{text}.yaml"
    if not path.is_file():
        known = ", ".join(sorted(item.stem for item in AGENTS_DIR.glob("*.yaml")))
        raise ValueError(f"unknown coding agent {ref!r} (catalog: {known})")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: an agent profile is a mapping")
    unknown = sorted(set(raw) - _AGENT_FIELDS)
    if unknown:
        raise ValueError(f"{path}: unknown agent field(s): {', '.join(unknown)}")
    backend = raw.get("backend")
    if backend not in BACKENDS:
        raise ValueError(f"{path}: backend must be one of {', '.join(BACKENDS)}")
    executable = str(raw.get("executable") or "").strip()
    if not executable:
        raise ValueError(f"{path}: executable is required")
    model = str(raw["model"]).strip() if raw.get("model") else None
    credential_file = None
    if backend == "claude":
        if not raw.get("credential_file"):
            raise ValueError(f"{path}: a claude agent needs credential_file (its subscription token)")
        credential_file = Path(str(raw["credential_file"])).expanduser()
    elif raw.get("credential_file") is not None:
        raise ValueError(f"{path}: credential_file is for the claude backend")
    if raw.get("service_tier") is not None and backend != "codex":
        raise ValueError(f"{path}: service_tier is for the codex backend")
    return AgentProfile(
        name=path.stem,
        backend=backend,
        executable=executable,
        model=model,
        reasoning_effort=str(raw.get("reasoning_effort") or "high"),
        credential_file=credential_file,
        service_tier=str(raw["service_tier"]).strip() if raw.get("service_tier") else None,
    )


def create_coding_session(
    *,
    agent: AgentProfile,
    root: Path,
    run_dir: Path,
    thread_id: str | None = None,
    turn_timeout_s: float = 90 * 60,
    full_access: bool = False,
    environment: Mapping[str, str] | None = None,
    on_thread_started: Callable[[str], None] | None = None,
    record_events: bool = True,
    web_search: bool = False,
) -> CodingSession:
    """Create one session of the agent's CLI and prevent cross-provider session-id reuse."""

    backend = agent.backend
    executable = str(agent.resolved_executable())
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    marker = run_dir / "agent_backend.json"
    record = _read_marker(marker)
    previous = record.get("backend")
    if thread_id and previous is not None and previous != backend:
        raise ValueError(
            f"cached session uses {previous!r}, not {backend!r}; rerun with --fresh"
        )
    # Every agent configuration that has driven this run, in order, so what a finished
    # run was done with stays on record next to its event log.
    current = {"agent": agent.name, "model": agent.model,
               "reasoning_effort": agent.reasoning_effort}
    agents = [item for item in record.get("agents") or [] if isinstance(item, dict)]
    if not agents or agents[-1] != current:
        agents.append(current)
    marker.write_text(
        json.dumps({"backend": backend, "agents": agents}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    common = {
        "root": root,
        "run_dir": run_dir,
        "thread_id": thread_id,
        "model": agent.model,
        "reasoning_effort": agent.reasoning_effort,
        "turn_timeout_s": turn_timeout_s,
        "full_access": full_access,
        "environment": environment,
        "on_thread_started": on_thread_started,
        "record_events": record_events,
        "web_search": web_search,
    }
    if backend == "codex":
        return CodexSession(codex=executable, service_tier=agent.service_tier, **common)
    if backend == "claude":
        from tools.claude_session import ClaudeSession

        return ClaudeSession(claude=executable, credential_file=agent.credential_file, **common)


def agent_usage(run_dir: Path) -> dict[str, int] | None:
    """Token totals an agent run logged, whichever CLI ran it; None without a report."""
    from tools.claude_session import claude_usage
    from tools.codex_session import logged_usage

    usages = [usage for usage in (logged_usage(run_dir / "codex.jsonl"),
                                  claude_usage(run_dir / "claude.jsonl")) if usage]
    if not usages:
        return None
    totals: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            totals[key] = totals.get(key, 0) + value
    return totals


def _read_marker(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


__all__ = [
    "AgentProfile",
    "CodingProcessError",
    "CodingProtocolError",
    "CodingSession",
    "CodingSessionError",
    "agent_usage",
    "create_coding_session",
    "load_agent",
]
