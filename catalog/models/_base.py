"""Typed computer-use model catalog records.

A model profile says where a model is and how to call it: the wire protocol, how the
model emits actions, how to authenticate, provider-specific request fields, which
thinking levels it offers and what each level sends, and -- for a model served
locally -- which weights and server arguments to use. How a run uses the model
(sampling, thinking level, step budget) belongs to the run plan, not here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .._yaml import (
    as_float,
    as_int,
    as_mapping,
    as_optional_text,
    as_string_list,
    as_text,
)


# http_chat: an OpenAI-compatible chat completions server; claude_cli: the Claude Code
# CLI on a Claude subscription (api_key_file holds its `claude setup-token` token);
# codex_cli: the Codex CLI on the machine's own Codex login.
_BACKENDS = {"http_chat", "claude_cli", "codex_cli"}
# qwen: actions as <tool_call> text with 0-1000 coordinates (also the training format);
# canonical_tool: OpenAI function calling with viewport pixel coordinates.
_CODECS = {"qwen", "canonical_tool"}
_IMAGE_DETAILS = {"auto", "low", "high", "original"}
_KNOWN_FIELDS = {
    "model_name",
    "backend",
    "codec",
    "model",
    "base_url",
    "endpoint",
    "api_key_env",
    "api_key_file",
    "headers",
    "max_tokens",
    "request_timeout",
    "image_detail",
    "request_options",
    "unsupported_params",
    "thinking",
    "serve",
}
_SERVE_FIELDS = {"weights", "revision", "args", "thinking_args"}


def thinking_level(value: Any) -> str:
    """A thinking level name. YAML reads a bare off/on as false/true; they mean the
    level names "off" and "on"."""
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value).strip()
    if not text:
        raise ValueError("thinking level must not be empty")
    return text


@dataclass(slots=True, frozen=True)
class ModelServe:
    """How tools.rollout serves a local model with vLLM."""

    weights: str
    revision: str | None = None
    args: tuple[str, ...] = ()
    # Added only when the run thinks (e.g. a reasoning parser that splits the thinking
    # from the answer); a run without thinking starts the server exactly with `args`.
    thinking_args: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ModelServe:
        raw = as_mapping(data)
        unknown = sorted(set(raw) - _SERVE_FIELDS)
        if unknown:
            raise ValueError(f"Unknown serve field(s): {', '.join(unknown)}")
        weights = as_optional_text(raw.get("weights"))
        if not weights:
            raise ValueError("serve.weights is required")
        return cls(
            weights=weights,
            revision=as_optional_text(raw.get("revision")),
            args=tuple(str(item) for item in raw.get("args") or ()),
            thinking_args=tuple(str(item) for item in raw.get("thinking_args") or ()),
        )


@dataclass(slots=True, frozen=True)
class ModelProfile:
    """Everything needed to construct one CUA client from YAML."""

    model_name: str
    backend: str
    codec: str
    model: str
    base_url: str | None = None
    endpoint: str | None = None
    api_key_env: tuple[str, ...] = ()
    api_key_file: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    max_tokens: int = 4096
    request_timeout: float = 120.0
    image_detail: str = "auto"
    request_options: dict[str, Any] = field(default_factory=dict)
    # Request fields this model rejects; removed from every request it is sent.
    unsupported_params: tuple[str, ...] = ()
    # Thinking level -> request fields that select it. A model lists only the levels
    # it really offers (one that cannot stop thinking has no "off").
    thinking: dict[str, dict[str, Any]] = field(default_factory=lambda: {"off": {}})
    serve: ModelServe | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> ModelProfile:
        raw = as_mapping(data)
        unknown = sorted(set(raw) - _KNOWN_FIELDS)
        if unknown:
            raise ValueError(f"Unknown model profile field(s): {', '.join(unknown)}")

        required = {
            name: as_optional_text(raw.get(name))
            for name in ("model_name", "backend", "codec")
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Model profile missing required field(s): {', '.join(missing)}")
        backend = required["backend"] or ""
        codec = required["codec"] or ""
        if backend not in _BACKENDS:
            raise ValueError(f"Unsupported model backend: {backend}")
        model = as_optional_text(raw.get("model"))
        if backend == "http_chat" and not model:
            raise ValueError("an HTTP model profile requires model")
        if codec not in _CODECS:
            raise ValueError(f"Unsupported model codec: {codec}")
        if backend in ("claude_cli", "codex_cli") and codec != "canonical_tool":
            raise ValueError(f"the {backend} backend acts through the canonical_tool codec")

        image_detail = as_text(raw.get("image_detail"), default="auto").strip()
        if image_detail not in _IMAGE_DETAILS:
            raise ValueError(f"Unsupported image_detail: {image_detail}")
        max_tokens = as_int(raw.get("max_tokens"), default=4096)
        request_timeout = as_float(raw.get("request_timeout"), default=120.0)
        if max_tokens <= 0:
            raise ValueError("max_tokens must be greater than zero")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be greater than zero")

        thinking_raw = raw.get("thinking")
        if thinking_raw is None:
            thinking: dict[str, dict[str, Any]] = {"off": {}}
        else:
            thinking = {}
            for level, fields in as_mapping(thinking_raw).items():
                if fields is not None and not isinstance(fields, Mapping):
                    raise ValueError(f"thinking.{level} must map to request fields")
                thinking[thinking_level(level)] = dict(fields or {})
            if not thinking:
                raise ValueError("thinking must list at least one level")

        serve = ModelServe.from_mapping(raw["serve"]) if raw.get("serve") is not None else None
        base_url = as_optional_text(raw.get("base_url"))
        endpoint = as_optional_text(raw.get("endpoint"))
        if backend == "http_chat" and serve is None and not (base_url or endpoint):
            raise ValueError("a model needs base_url or endpoint, or serve for a local model")
        headers = as_mapping(raw.get("headers"))

        return cls(
            model_name=required["model_name"] or "",
            backend=backend,
            codec=codec,
            model=model or "",
            base_url=base_url,
            endpoint=endpoint,
            api_key_env=tuple(as_string_list(raw.get("api_key_env"))),
            api_key_file=as_optional_text(raw.get("api_key_file")),
            headers={str(key): str(value) for key, value in headers.items()},
            max_tokens=max_tokens,
            request_timeout=request_timeout,
            image_detail=image_detail,
            request_options=as_mapping(raw.get("request_options")),
            unsupported_params=tuple(as_string_list(raw.get("unsupported_params"))),
            thinking=thinking,
            serve=serve,
        )

    def thinking_request(self, level: str) -> dict[str, Any]:
        """The request fields that select a thinking level this model offers."""
        if level not in self.thinking:
            offered = ", ".join(self.thinking)
            raise ValueError(f"{self.model_name} offers thinking levels {offered}, not {level!r}")
        return dict(self.thinking[level])

    def resolve_thinking(self, level: Any = None) -> str:
        """Use the profile's first listed level only when none was requested."""
        selected = next(iter(self.thinking)) if level is None else thinking_level(level)
        self.thinking_request(selected)
        return selected

    def served_at(self, endpoint: str) -> ModelProfile:
        """This profile pointed at a running local server."""
        return replace(self, endpoint=endpoint, base_url=None)

    def to_mapping(self) -> dict[str, Any]:
        """The profile as YAML fields (never a secret: keys are named, not included)."""
        out: dict[str, Any] = {
            "model_name": self.model_name,
            "backend": self.backend,
            "codec": self.codec,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "request_timeout": self.request_timeout,
            "image_detail": self.image_detail,
            "thinking": {level: dict(fields) for level, fields in self.thinking.items()},
        }
        for name in ("base_url", "endpoint", "api_key_file"):
            if getattr(self, name):
                out[name] = getattr(self, name)
        if self.api_key_env:
            out["api_key_env"] = list(self.api_key_env)
        if self.headers:
            out["headers"] = dict(self.headers)
        if self.request_options:
            out["request_options"] = dict(self.request_options)
        if self.unsupported_params:
            out["unsupported_params"] = list(self.unsupported_params)
        if self.serve is not None:
            serve: dict[str, Any] = {"weights": self.serve.weights}
            if self.serve.revision:
                serve["revision"] = self.serve.revision
            if self.serve.args:
                serve["args"] = list(self.serve.args)
            if self.serve.thinking_args:
                serve["thinking_args"] = list(self.serve.thinking_args)
            out["serve"] = serve
        return out


__all__ = ["ModelProfile", "ModelServe", "thinking_level"]
