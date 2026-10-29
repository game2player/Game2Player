"""One computer-use client lifecycle independent of model provider."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PIL import Image

from .backends import Backend
from .codecs import chat_tool_arguments
from .types import Action, ContextEntry, CUAClientConfig, ModelRateLimitError, ModelRequestError, ModelUsageLimitError, Observation



def _no_action_hint(response: Any) -> str:
    """Say *why* a turn produced no action, using the provider's own response.

    Without this the warning is identical whether the model was truncated
    mid-JSON, returned prose, or deliberately emitted an empty action list --
    and the response itself is not persisted anywhere in the episode.
    """
    if not isinstance(response, Mapping):
        return ""
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, Mapping):
        return ""
    message = choice.get("message")
    content = ""
    if isinstance(message, Mapping):
        content = str(message.get("content") or "").strip()
    parts = [f"finish_reason={choice.get('finish_reason')!r}"]
    usage = response.get("usage")
    if isinstance(usage, Mapping):
        parts.append(f"completion_tokens={usage.get('completion_tokens')}")
    parts.append(
        f"content={content[:300]!r}" if content else "content=<empty>"
    )
    return " (" + ", ".join(parts) + ")"


class CUAClient:
    """Stateless model transport; agent versions own context and control logic."""

    def __init__(self, config: CUAClientConfig, backend: Backend):
        self.config = config
        self.backend = backend
        self._logger = logging.getLogger(f"CUAClient.{config.profile.model_name}")
        self._last_interaction: dict[str, Any] | None = None

    @staticmethod
    def _observation(path: Path) -> Observation:
        raw = path.read_bytes()
        with Image.open(path) as image:
            width, height = image.size
            mime = Image.MIME[image.format]
        encoded = base64.b64encode(raw).decode("ascii")
        return Observation(
            screenshot_path=path,
            width=width,
            height=height,
            data_url=f"data:{mime};base64,{encoded}",
            image_base64=encoded,
        )

    @classmethod
    def _plain(cls, value: Any, seen: set[int] | None = None) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (bytes, bytearray)):
            return "<binary>"
        active = seen if seen is not None else set()
        marker = id(value)
        if marker in active:
            return "<circular>"
        active.add(marker)
        try:
            if isinstance(value, dict):
                return {str(key): cls._plain(item, active) for key, item in value.items()}
            if isinstance(value, (list, tuple, set)):
                return [cls._plain(item, active) for item in value]
            for method_name in ("model_dump", "to_dict"):
                method = getattr(value, method_name, None)
                if callable(method):
                    try:
                        return cls._plain(method(), active)
                    except Exception:
                        pass
            raw_dict = getattr(value, "__dict__", None)
            if isinstance(raw_dict, dict):
                return cls._plain(raw_dict, active)
            return str(value)
        finally:
            active.discard(marker)

    @classmethod
    def _redact_images(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): cls._redact_images(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._redact_images(item) for item in value]
        if isinstance(value, str):
            if value.lower().startswith("data:image/") and ";base64," in value.lower():
                return "<image>"
            if len(value) > 512 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", value):
                return "<binary>"
        return value

    @classmethod
    def _stringify_request(cls, value: Any) -> str:
        return json.dumps(
            cls._redact_images(cls._plain(value)),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    @classmethod
    def _stringify_response(cls, value: Any) -> str:
        plain = cls._plain(value)
        if isinstance(plain, (dict, list)):
            return json.dumps(plain, ensure_ascii=False, default=str)
        return str(plain)

    def _set_trace(
        self,
        *,
        screenshot: Path,
        request: Any,
        response: Any,
        actions: list[Action],
        thinking: str | None,
        error: str | None,
        context_entries: list[ContextEntry],
        finish_reason: str | None = None,
    ) -> None:
        response_text = ""
        usage = response.get("usage") if isinstance(response, Mapping) else None
        if isinstance(response, Mapping):
            choices = response.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else None
            message = choice.get("message") if isinstance(choice, Mapping) else None
            if isinstance(message, Mapping):
                response_text = str(message.get("content") or "")
                if message.get("tool_calls"):
                    calls = json.dumps(message["tool_calls"], ensure_ascii=False, default=str)
                    response_text = f"{response_text}\n{calls}" if response_text else calls
        arguments = chat_tool_arguments(response)
        if arguments is not None and "note" not in arguments and isinstance(arguments.get("reasoning"), str):
            arguments["note"] = arguments["reasoning"]
        self._last_interaction = {
            "screenshot_path": screenshot,
            "prompt": self.config.system_prompt,
            "system_prompt": self.config.system_prompt,
            "user_prompt": "Current game screen:",
            "raw_message_sent": self._stringify_request(request),
            "raw_response": self._stringify_response(response),
            "response_text": response_text,
            "parsed_action": actions[0] if len(actions) == 1 else None,
            "parsed_actions": list(actions),
            "error": error,
            "context_entries": list(context_entries),
            "model_name": self.config.model,
            "thinking": thinking,
            "note": arguments.get("note") if arguments else None,
            "finish_reason": finish_reason,
            # The computer_use call's own arguments (its short note, review
            # `notes`), which parsing strips from the action.
            "tool_arguments": arguments,
            "usage": self._plain(usage) if isinstance(usage, Mapping) else None,
        }

    def get_actions(
        self,
        screenshot_path: Path,
        context_entries: list[ContextEntry] | None = None,
    ) -> list[Action]:
        observation = self._observation(screenshot_path)
        context = list(context_entries or [])
        for attempt in range(1, 4):
            try:
                turn = self.backend.run(
                    self.config.profile,
                    observation,
                    self.config.system_prompt,
                    context,
                    self.config.action_contract,
                )
                break
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Model request attempt %s/3 failed: %s", attempt, exc)
                # A plan limit fails every retry until it resets.
                if attempt < 3 and not isinstance(exc, ModelUsageLimitError):
                    time.sleep(exc.retry_after if isinstance(exc, ModelRateLimitError) else 5 * attempt)
                    continue
                self._set_trace(
                    screenshot=screenshot_path,
                    request=None,
                    response=None,
                    actions=[],
                    thinking=None,
                    error=str(exc),
                    context_entries=context,
                )
                raise ModelRequestError(f"Model request failed after {attempt} attempt{"s" if attempt > 1 else ""}: {exc}") from exc

        error = turn.error
        actions: list[Action] = []
        if not error and not turn.actions:
            error = "Expected at least one action, received none" + _no_action_hint(
                turn.response
            )
        elif not error:
            try:
                actions = self.config.action_contract.normalize(turn.actions).actions
            except ValueError as exc:
                error = str(exc)

        self._set_trace(
            screenshot=screenshot_path,
            request=turn.request,
            response=turn.response,
            actions=actions,
            thinking=turn.thinking,
            error=error,
            context_entries=context,
            finish_reason=turn.finish_reason,
        )
        if error:
            self._logger.warning("%s", error)
        return actions

    def pop_logged_interaction(self) -> dict[str, Any] | None:
        interaction = self._last_interaction
        self._last_interaction = None
        return interaction


__all__ = ["CUAClient"]
