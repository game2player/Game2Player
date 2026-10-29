"""Persistent, read-only game discovery agent."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from tools.coding_session import AgentProfile, CodingProtocolError, create_coding_session

from .builder import ENGINES


MAX_PROTOCOL_REPAIRS = 2


class ScoutProtocolError(RuntimeError):
    """A Scout turn returned a result outside its campaign contract."""


CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "campaign": {"type": "string"},
        "summary": {"type": "string"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_kind": {
                        "type": "string",
                        "enum": ["itch", "github", "flash_archive", "other"],
                    },
                    "engine": {
                        "type": "string",
                        "enum": list(ENGINES),
                    },
                    "genre": {"type": "string"},
                    "dimension": {
                        "type": "string",
                        "enum": ["2d", "2.5d", "3d", "unknown"],
                    },
                    "play_evidence": {"type": "array", "items": {"type": "string"}},
                    "quality_evidence": {"type": "array", "items": {"type": "string"}},
                    "offline_outlook": {
                        "type": "string",
                        "enum": ["strong", "plausible", "weak", "unknown"],
                    },
                    "state_outlook": {
                        "type": "string",
                        "enum": ["strong", "plausible", "weak", "unknown"],
                    },
                    "task_ideas": {"type": "array", "items": {"type": "string"}},
                    "rights_note": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": ["recommend", "reserve", "reject"],
                    },
                    "decision_reason": {"type": "string"},
                },
                "required": [
                    "title",
                    "source_url",
                    "source_kind",
                    "engine",
                    "genre",
                    "dimension",
                    "play_evidence",
                    "quality_evidence",
                    "offline_outlook",
                    "state_outlook",
                    "task_ideas",
                    "rights_note",
                    "decision",
                    "decision_reason",
                ],
                "additionalProperties": False,
            },
        },
        "coverage_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["campaign", "summary", "candidates", "coverage_gaps"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class ScoutState:
    campaign: str
    # Without the brief a finished campaign cannot be read back: its reports answer a
    # question the record does not contain. BuilderState has kept its brief all along.
    brief: str = ""
    thread_id: str | None = None
    turns: int = 0
    reports: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, campaign: str) -> ScoutState:
        if not path.is_file():
            return cls(campaign=campaign)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("campaign") != campaign:
            raise ValueError("cached Scout campaign does not match")
        return cls(
            campaign=campaign,
            brief=str(raw.get("brief") or ""),
            thread_id=raw.get("thread_id"),
            turns=int(raw.get("turns") or 0),
            reports=list(raw.get("reports") or []),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "campaign": self.campaign,
                    "brief": self.brief,
                    "thread_id": self.thread_id,
                    "turns": self.turns,
                    "reports": self.reports,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


class Scout:
    """Run one research turn in a resumable campaign without editing the repo."""

    def __init__(
        self,
        campaign: str,
        *,
        root: Path | None = None,
        agent: AgentProfile,
        turn_timeout_s: float = 45 * 60,
        full_access: bool = False,
        environment: Mapping[str, str] | None = None,
        fresh: bool = False,
    ):
        if full_access:
            raise ValueError(
                "Scout is read-only; use its web search tool instead of --full-access"
            )
        self.root = (root or Path(__file__).resolve().parents[2]).resolve()
        self.campaign = _safe_name(campaign, "campaign")
        self.run_dir = (
            self.root / ".cache" / "environment-factory" / "scout" / self.campaign
        )
        if fresh and self.run_dir.exists():
            shutil.rmtree(self.run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        # Where the Scout keeps what it downloads and writes to try a game.
        self.scratch_dir = self.run_dir / "scratch"
        self.scratch_dir.mkdir(exist_ok=True)
        self.state_path = self.run_dir / "state.json"
        self.state = ScoutState.load(self.state_path, self.campaign)
        self.session = create_coding_session(
            agent=agent,
            root=self.root,
            run_dir=self.run_dir,
            thread_id=self.state.thread_id,
            turn_timeout_s=turn_timeout_s,
            full_access=full_access,
            environment=environment,
            on_thread_started=self._remember_thread,
            web_search=True,
        )

    def run(self, brief: str) -> dict[str, Any]:
        brief = brief.strip()
        if not brief:
            raise ValueError("Scout brief must not be empty")
        self.state.brief = brief
        if self.state.thread_id:
            prompt = (
                "Continue the same scouting campaign. Avoid every candidate already "
                "reported unless new evidence materially changes its decision.\n\n"
                f"Current research brief:\n{brief}"
            )
        else:
            template = (Path(__file__).with_name("prompts") / "scout.md").read_text(
                encoding="utf-8"
            )
            prompt = template.format(campaign=self.campaign, brief=brief,
                                     scratch_dir=self.scratch_dir)
        last_error: Exception | None = None
        for attempt in range(MAX_PROTOCOL_REPAIRS + 1):
            try:
                report = self.session.turn(
                    prompt,
                    CANDIDATE_SCHEMA,
                    sandbox="read-only",
                )
                if report.get("campaign") != self.campaign:
                    raise ScoutProtocolError("Scout returned the wrong campaign name")
                break
            except (CodingProtocolError, ScoutProtocolError) as exc:
                last_error = exc
                if attempt >= MAX_PROTOCOL_REPAIRS or not self.session.thread_id:
                    raise
                prompt = (
                    "The runner rejected your previous report:\n\n"
                    f"{exc}\n\nReturn a corrected report for campaign "
                    f"{self.campaign!r}. Preserve the research findings and use the "
                    "required structured schema."
                )
        else:  # pragma: no cover - the bounded loop always returns or raises
            assert last_error is not None
            raise last_error
        self.state.turns += 1
        self.state.reports.append(report)
        self.state.save(self.state_path)
        return report

    def _remember_thread(self, thread_id: str) -> None:
        self.state.thread_id = thread_id
        self.state.save(self.state_path)


def _safe_name(value: str, label: str) -> str:
    value = value.strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if not value or Path(value).name != value or any(c not in allowed for c in value):
        raise ValueError(f"{label} may contain only letters, digits, '-' and '_'")
    return value


__all__ = ["CANDIDATE_SCHEMA", "Scout", "ScoutState"]
