"""Computer use through the Claude Code CLI, on a Claude subscription.

Each decision is one `claude --print` run with no tools and no saved session: the
harness's system prompt replaces Claude Code's own, the context and the screen arrive
as one user message (images included), and the action is the run's structured output
under the computer_use schema. The CLI runs in a private configuration directory with
the subscription token, so it reads nothing of the machine's own Claude setup.

The answer is returned in the chat-completions shape the client and the harnesses
read (one computer_use call, usage in their token names), with the CLI's own final
event kept beside it.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from harnesses.actions import ActionContract
from tools.paths import temporary_dir
from tools.claude_session import claude_environment, claude_installs, claude_limit_reset
from .backends import Backend, _api_key, _openai_content
from .codecs import canonical_tool, parse_chat_tool_action
from .cli_process import run_cli
from .types import ContextEntry, ModelProfileLike, ModelTurn, ModelUsageLimitError, Observation


class ClaudeCliBackend(Backend):
    def __init__(self) -> None:
        # One private configuration directory per client, removed with it.
        self._home = tempfile.TemporaryDirectory(prefix="claude-", dir=temporary_dir("cli"))

    def run(
        self,
        profile: ModelProfileLike,
        observation: Observation,
        system_prompt: str | None,
        context_entries: list[ContextEntry],
        action_contract: ActionContract,
    ) -> ModelTurn:
        token = _api_key(profile)
        if not token:
            raise ValueError(f"{profile.model_name}: name the `claude setup-token` token "
                             "in api_key_file")
        options = dict(profile.request_options)
        for name in profile.unsupported_params:
            options.pop(name, None)
        effort = options.pop("effort", None)
        max_tokens = options.pop("max_tokens", profile.max_tokens)
        if options:
            raise ValueError(f"{profile.model_name}: the Claude CLI cannot set "
                             f"{', '.join(sorted(options))}")

        tool = canonical_tool(chat_format=True, width=observation.width,
                              height=observation.height, contract=action_contract)["function"]
        schema = {**tool["parameters"], "description": tool["description"]}
        command = [
            _claude(), "--print", "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose",
            "--tools", "", "--json-schema", json.dumps(schema),
            "--no-session-persistence", "--strict-mcp-config",
        ]
        if profile.model:
            command.extend(["--model", profile.model])
        if system_prompt:
            command.extend(["--system-prompt", system_prompt])
        if effort:
            command.extend(["--effort", str(effort)])
        content = [
            _claude_block(part)
            for part in _openai_content(observation, context_entries,
                                        user_prompt="Current game screen:", image_detail="auto")
        ]
        message = {"type": "user", "message": {"role": "user", "content": content}}

        home = Path(self._home.name)
        environment = claude_environment(None, config_dir=home / "config", token=token)
        environment["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(int(max_tokens))
        try:
            events, returncode, stderr = run_cli(
                command, prompt=json.dumps(message) + "\n", cwd=home,
                timeout=profile.request_timeout, label="Claude CLI", environment=environment,
                failed=lambda event: event.get("type") == "result" and bool(event.get("is_error")))
        except RuntimeError as exc:
            resets = claude_limit_reset(str(exc))
            if resets is not None:
                raise ModelUsageLimitError(str(exc), resets) from exc
            raise
        result = next((event for event in reversed(events) if event.get("type") == "result"), None)
        if result is None or result.get("is_error") or returncode != 0:
            detail = ((result or {}).get("result") or stderr.strip()[-500:]
                      or f"exit status {returncode}")
            resets = claude_limit_reset(str(detail))
            if resets is not None:
                raise ModelUsageLimitError(f"Claude CLI request failed: {detail}", resets)
            raise RuntimeError(f"Claude CLI request failed: {detail}")

        arguments = result.get("structured_output")
        reply: dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": []}
        if isinstance(arguments, dict):
            reply["tool_calls"].append({"type": "function", "function": {
                "name": "computer_use", "arguments": json.dumps(arguments, ensure_ascii=False)}})
        thinking = "\n".join(
            block["thinking"]
            for event in events if event.get("type") == "assistant"
            for block in (event.get("message") or {}).get("content") or []
            if isinstance(block, dict) and block.get("type") == "thinking"
            and isinstance(block.get("thinking"), str) and block["thinking"].strip()
        )
        if thinking:
            reply["reasoning_content"] = thinking
        finish_reason = result.get("stop_reason")
        response = {
            "choices": [{"message": reply, "finish_reason": finish_reason}],
            "usage": _usage(result.get("usage")),
            "claude_result": result,
        }
        actions, reasoning = parse_chat_tool_action(response)
        return ModelTurn(
            actions=actions,
            thinking=reasoning,
            request={"command": command, "message": message},
            response=response,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )


def _claude() -> str:
    for candidate in ("claude", *claude_installs()):
        if os.sep in candidate:
            if os.access(candidate, os.X_OK):
                return candidate
        elif shutil.which(candidate):
            return str(shutil.which(candidate))
    raise FileNotFoundError("the Claude Code CLI (claude) was not found")


def _claude_block(part: dict[str, Any]) -> dict[str, Any]:
    """An OpenAI content part as a Claude one: images are inline base64 PNGs."""
    if part.get("type") != "image_url":
        return part
    header, _, data = str(part["image_url"]["url"]).partition(",")
    media_type = header.removeprefix("data:").removesuffix(";base64")
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _usage(usage: Any) -> dict[str, Any] | None:
    """Claude's token counts in the names the other backends report: prompt tokens
    include the cached and newly cached input, completion tokens include thinking."""
    if not isinstance(usage, dict):
        return None
    cached = int(usage.get("cache_read_input_tokens") or 0)
    prompt = (int(usage.get("input_tokens") or 0) + cached
              + int(usage.get("cache_creation_input_tokens") or 0))
    completion = int(usage.get("output_tokens") or 0)
    details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cached},
        "completion_tokens_details": {"reasoning_tokens": int(details.get("thinking_tokens") or 0)},
    }


__all__ = ["ClaudeCliBackend"]
