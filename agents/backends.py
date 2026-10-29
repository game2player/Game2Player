"""The model wire protocol: OpenAI-compatible chat completions.

A profile's codec decides how the model returns its action: `qwen` as <tool_call> text
(the training format), `canonical_tool` as a computer_use function call.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from harnesses.actions import ActionContract
from .qwen_prompting import (
    build_qwen_user_content,
    canonical_action_to_qwen_args,
)
from .codecs import (
    canonical_tool,
    chat_message_thinking,
    parse_chat_tool_action,
    parse_qwen_action,
)
from .types import ContextEntry, ModelProfileLike, ModelRateLimitError, ModelTurn, Observation


def _api_key(profile: ModelProfileLike) -> str | None:
    """The key from the first set environment variable, else from the key file (which
    must be private to its owner); None when the profile names neither."""
    for name in profile.api_key_env:
        value = os.environ.get(name)
        if value:
            return value
    if profile.api_key_file:
        path = Path(profile.api_key_file).expanduser()
        if not path.is_file():
            raise ValueError(f"{profile.model_name}: key file not found: {path}")
        if path.stat().st_mode & 0o077:
            raise ValueError(f"{profile.model_name}: key file must be readable only by its owner: {path}")
        key = path.read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError(f"{profile.model_name}: key file is empty: {path}")
        return key
    if profile.api_key_env:
        names = ", ".join(profile.api_key_env)
        raise ValueError(
            f"{profile.model_name} requires one of these environment variables: {names}"
        )
    return None


def _endpoint(profile: ModelProfileLike, default_base: str, path: str) -> str:
    if profile.endpoint:
        return profile.endpoint
    return f"{(profile.base_url or default_base).rstrip('/')}/{path.lstrip('/')}"


# Status codes worth a second attempt (524: the provider's gateway gave up waiting for
# the upstream model). Every other 4xx is the caller's fault and retrying it only burns
# the episode's wall clock.
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 524})

# A dropped connection or a read timeout is by far the most common way a long run
# loses an episode, so both are retried. Everything else (a bad URL, too many
# redirects) will fail the same way on the next attempt.
_TRANSIENT_EXCEPTIONS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def _retry_settings() -> tuple[int, float, float]:
    """Attempts and backoff, tunable per run without editing this file."""
    attempts = max(1, int(os.environ.get("GAMEENV_HTTP_ATTEMPTS", "4")))
    base = max(0.0, float(os.environ.get("GAMEENV_HTTP_BACKOFF_S", "2")))
    cap = max(0.0, float(os.environ.get("GAMEENV_HTTP_BACKOFF_CAP_S", "30")))
    return attempts, base, cap


def _retry_after_s(response: requests.Response, cap: float) -> float | None:
    """The server's own wait hint, in seconds, when it gives one as a number."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(cap, max(0.0, float(raw.strip())))
    except ValueError:
        return None  # HTTP-date form; the computed backoff is close enough.


def _sleep_before_retry(delay: float, attempt: int, attempts: int, reason: str) -> None:
    # Full jitter, so parallel actors that trip the same outage do not all come
    # back at the same instant.
    wait = random.uniform(0.0, delay) if delay > 0 else 0.0
    print(
        f"[backend] attempt {attempt}/{attempts} failed ({reason}); "
        f"retrying in {wait:.1f}s",
        file=sys.stderr,
        flush=True,
    )
    if wait > 0:
        time.sleep(wait)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None,
    timeout: float,
) -> dict[str, Any]:
    """POST with bounded retries on transport faults and retryable status codes.

    A read timeout may mean the provider did receive the request, so a retry can
    bill a second completion. That is the cheaper failure: without it a single
    blip discards the whole episode that led up to it.
    """
    attempts, base, cap = _retry_settings()
    for attempt in range(1, attempts + 1):
        final = attempt == attempts
        backoff = min(cap, base * (2 ** (attempt - 1))) if base else 0.0
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            if final or not isinstance(exc, _TRANSIENT_EXCEPTIONS):
                raise RuntimeError(f"Model request failed: {exc}") from exc
            _sleep_before_retry(backoff, attempt, attempts, type(exc).__name__)
            continue
        if not response.ok:
            body = response.text.strip().replace("\n", " ")[:2000]
            if final or response.status_code not in _RETRY_STATUS:
                if response.status_code == 429:
                    delay = _retry_after_s(response, float("inf"))
                    raise ModelRateLimitError(f"Model HTTP 429: {body}",
                                              retry_after=65.0 if delay is None else delay)
                raise RuntimeError(f"Model HTTP {response.status_code}: {body}")
            hinted = _retry_after_s(response, max(cap, 60.0))
            _sleep_before_retry(
                backoff if hinted is None else hinted,
                attempt,
                attempts,
                f"HTTP {response.status_code}: {body[:200]}",
            )
            continue
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Model returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Model response root must be a JSON object")
        return data
    raise AssertionError("unreachable: the final attempt either returns or raises")


def _context_text(entry: ContextEntry) -> str | None:
    if entry.type != "text" or not entry.text:
        return None
    label = (entry.field or entry.role).replace("_", " ").title()
    return f"{label}: {entry.text.strip()}"


def _openai_content(
    observation: Observation,
    context_entries: list[ContextEntry],
    *,
    user_prompt: str,
    image_detail: str,
) -> list[dict[str, Any]]:
    text_type = "text"
    image_type = "image_url"
    content: list[dict[str, Any]] = []
    if context_entries:
        content.append({"type": text_type, "text": "## Agent-provided context"})
    for entry in context_entries:
        text = _context_text(entry)
        if text:
            content.append({"type": text_type, "text": text})
            continue
        image = entry.image_file() if entry.type == "image" else None
        if image and image.exists():
            import base64

            encoded = base64.b64encode(image.read_bytes()).decode("ascii")
            with Image.open(image) as source:
                mime = Image.MIME[source.format]
            data_url = f"data:{mime};base64,{encoded}"
            image_url = {"url": data_url}
            if image_detail != "auto":
                image_url["detail"] = image_detail
            content.append({"type": image_type, "image_url": image_url})
    content.append({"type": text_type, "text": user_prompt})
    image_url = {"url": observation.data_url}
    if image_detail != "auto":
        image_url["detail"] = image_detail
    content.append({"type": image_type, "image_url": image_url})
    return content


def _qwen_history(
    context_entries: list[ContextEntry],
    *,
    image_detail: str,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    """Turn the harness memory stream into labelled chronological steps."""
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for entry in context_entries:
        if (
            entry.field in ("user_prompt", "step")
            or (entry.type == "image" and "image" in current)
        ) and current:
            turns.append(current)
            current = {}
        if entry.field == "step" and entry.text:
            current["step"] = entry.text
        elif entry.field == "screen" and entry.text:
            current["screen"] = entry.text
        elif entry.field == "note" and entry.text:
            current["note"] = entry.text
        elif entry.field == "feedback" and entry.text:
            current["feedback"] = entry.text
        elif entry.type == "image":
            image = entry.image_file()
            if image and image.exists():
                import base64

                encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                with Image.open(image) as source:
                    mime = Image.MIME[source.format]
                image_url: dict[str, Any] = {
                    "url": f"data:{mime};base64,{encoded}"
                }
                if image_detail != "auto":
                    image_url["detail"] = image_detail
                current["image"] = {"type": "image_url", "image_url": image_url}
        elif entry.field == "action" and entry.text:
            try:
                raw_action = json.loads(entry.text)
                current["action"] = json.dumps(
                    canonical_action_to_qwen_args(
                        raw_action,
                        width=width,
                        height=height,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                current["action"] = entry.text
    if current:
        turns.append(current)
    return turns


class Backend(ABC):
    """One wire protocol; model identity remains in the catalog."""

    @abstractmethod
    def run(
        self,
        profile: ModelProfileLike,
        observation: Observation,
        system_prompt: str | None,
        context_entries: list[ContextEntry],
        action_contract: ActionContract,
    ) -> ModelTurn:
        raise NotImplementedError


class HttpChatBackend(Backend):
    def run(
        self,
        profile: ModelProfileLike,
        observation: Observation,
        system_prompt: str | None,
        context_entries: list[ContextEntry],
        action_contract: ActionContract,
    ) -> ModelTurn:
        key = _api_key(profile)
        prompt = system_prompt

        payload: dict[str, Any] = {
            "model": profile.model,
            "messages": [],
            "max_tokens": profile.max_tokens,
            **profile.request_options,
        }
        if prompt:
            payload["messages"].append({"role": "system", "content": prompt})
        if profile.codec == "qwen":
            current_image_url: dict[str, Any] = {"url": observation.data_url}
            if profile.image_detail != "auto":
                current_image_url["detail"] = profile.image_detail
            # The harness puts its notes and a review request after the history.
            tail = {entry.field: entry.text for entry in context_entries if entry.field in ("notes", "review")}
            user_content = build_qwen_user_content(
                current_image={
                    "type": "image_url",
                    "image_url": current_image_url,
                },
                history=_qwen_history(
                    [entry for entry in context_entries if entry.field not in ("notes", "review")],
                    image_detail=profile.image_detail,
                    width=observation.width,
                    height=observation.height,
                ),
                notes=tail.get("notes"),
                review=tail.get("review"),
            )
        else:
            user_content = _openai_content(
                observation,
                context_entries,
                user_prompt="Current game screen:",
                image_detail=profile.image_detail,
            )
        payload["messages"].append(
            {
                "role": "user",
                "content": user_content,
            }
        )
        if profile.codec == "canonical_tool":
            payload["tools"] = [
                canonical_tool(
                    chat_format=True,
                    width=observation.width,
                    height=observation.height,
                    contract=action_contract,
                )
            ]
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": "computer_use"},
            }
        for name in profile.unsupported_params:
            payload.pop(name, None)

        headers = {"Content-Type": "application/json", **profile.headers}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        response = _post_json(
            _endpoint(profile, "http://127.0.0.1:8088/v1", "chat/completions"),
            payload,
            headers=headers,
            timeout=profile.request_timeout,
        )
        choices = response.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        finish_reason = choice.get("finish_reason")
        if profile.codec == "canonical_tool":
            actions, reasoning = parse_chat_tool_action(response)
        elif profile.codec == "qwen":
            message = choice.get("message") or {}
            text = message.get("content", "") if isinstance(message, dict) else ""
            actions, reasoning = parse_qwen_action(
                str(text), observation.width, observation.height
            )
            reasoning = chat_message_thinking(message) or reasoning
        else:
            raise ValueError(f"Codec {profile.codec} is not valid for http_chat")
        return ModelTurn(
            actions=actions,
            thinking=reasoning,
            request=payload,
            response=response,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )


_BACKENDS: dict[str, type[Backend]] = {"http_chat": HttpChatBackend}


def create_backend(name: str) -> Backend:
    if name == "claude_cli":
        from .claude_cli import ClaudeCliBackend

        return ClaudeCliBackend()
    if name == "codex_cli":
        from .codex_cli import CodexCliBackend

        return CodexCliBackend()
    try:
        return _BACKENDS[name]()
    except KeyError as exc:
        raise ValueError(f"Unsupported model backend: {name}") from exc


__all__ = ["Backend", "create_backend"]
