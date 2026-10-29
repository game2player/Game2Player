"""Provider-neutral types for computer-use agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from harnesses.actions import Action, ActionContract


class ModelRequestError(RuntimeError):
    """A model backend failed to produce a response, independently of the game."""


class ModelRateLimitError(RuntimeError):
    """A temporary request limit, with a delay before retrying the same decision."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(f"[request-rate-limit] {message}")
        self.retry_after = retry_after


class ModelUsageLimitError(RuntimeError):
    """The model's subscription limit was reached; it resets at `resets_at` (epoch s).
    Retrying before then only fails again."""

    def __init__(self, message: str, resets_at: float, backend: str = "claude"):
        # One line that starts with a marker: it survives the scheduler's summary of
        # the failure (the last line, cut to 300 characters) and is what the pipeline
        # reads, so no other error text can pass for a limit.
        message = " ".join(message.split())
        super().__init__(f"[plan-limit backend={backend} resets_at={resets_at:.0f}] {message}")
        self.resets_at = resets_at
        self.backend = backend


class ModelProfileLike(Protocol):
    model_name: str
    backend: str
    codec: str
    model: str
    base_url: str | None
    endpoint: str | None
    api_key_env: tuple[str, ...]
    api_key_file: str | None
    headers: dict[str, str]
    max_tokens: int
    request_timeout: float
    image_detail: str
    request_options: dict[str, Any]
    unsupported_params: tuple[str, ...]


ContextType = Literal["text", "image"]
ContextRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """One model-context item supplied by an agent version."""

    type: ContextType
    role: ContextRole
    text: str | None = None
    image_path: str | None = None
    field: str | None = None

    def image_file(self) -> Path | None:
        return Path(self.image_path) if self.image_path else None


@dataclass(frozen=True, slots=True)
class Observation:
    """One model observation in viewport coordinates."""

    screenshot_path: Path
    width: int
    height: int
    data_url: str
    image_base64: str


@dataclass(slots=True)
class ModelTurn:
    """Normalized result of one provider request."""

    actions: list[Action] = field(default_factory=list)
    thinking: str | None = None
    request: Any = None
    response: Any = None
    error: str | None = None
    finish_reason: str | None = None


@dataclass(slots=True)
class CUAClientConfig:
    """Runtime settings layered on top of a catalog model profile."""

    profile: ModelProfileLike
    system_prompt: str | None = None
    action_contract: ActionContract = field(default_factory=ActionContract)
    log_dir: str = "logs"
    log_session_id: str | None = None
    log_root: str | None = None

    @property
    def model(self) -> str:
        return self.profile.model

    @property
    def model_type(self) -> str:
        return "computer_use"


__all__ = [
    "Action",
    "ContextEntry",
    "CUAClientConfig",
    "ModelRequestError",
    "ModelRateLimitError",
    "ModelProfileLike",
    "ModelTurn",
    "Observation",
]
