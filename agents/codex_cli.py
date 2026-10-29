"""Computer use through the Codex CLI, on the machine's own Codex (ChatGPT) login.

Each decision is one `codex exec` run with no shell or other tool and no saved session:
the harness's system prompt replaces Codex's own instructions, the context arrives as
text with its screens attached as images in order (each marked where it belongs), and
the action is the run's structured output under the computer_use schema. Codex holds a
schema strictly, so every field is required and the optional ones may be null; the
nulls are dropped again before the action is parsed.

The answer is returned in the chat-completions shape the client and the harnesses
read (one computer_use call, usage in their token names), with the CLI's own final
event kept beside it.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from harnesses.actions import ActionContract
from tools.paths import temporary_dir
from tools.codex_transport import chatgpt_https_args
from .backends import Backend, _openai_content
from .codecs import canonical_tool, parse_chat_tool_action
from .cli_process import run_cli
from .request_pacing import RequestPacer
from .types import ContextEntry, ModelProfileLike, ModelRateLimitError, ModelTurn, Observation


# Codex features a decision has no use for: it only looks and answers.
DISABLED_FEATURES = ("shell_tool", "multi_agent", "plugins", "apps", "computer_use",
                     "browser_use", "image_generation", "goals", "hooks")

# Disabling tools does not remove their instructions. A player only needs the
# harness instructions and the game history, not the coding agent's context.
PLAYER_CONTEXT_SETTINGS = {
    "skills.include_instructions": False,
    "include_permissions_instructions": False,
    "include_apps_instructions": False,
    "include_collaboration_mode_instructions": False,
    "include_environment_context": False,
    "features.multi_agent_v2.usage_hint_text": "",
    "features.multi_agent_v2.root_agent_usage_hint_text": "",
    "features.multi_agent_v2.multi_agent_mode_hint_text": "",
}


class CodexCliBackend(Backend):
    def __init__(self) -> None:
        # One private working directory per client, removed with it.
        self._home = tempfile.TemporaryDirectory(prefix="codex-", dir=temporary_dir("cli"))
        self._pacer = RequestPacer.from_environment()

    def run(
        self,
        profile: ModelProfileLike,
        observation: Observation,
        system_prompt: str | None,
        context_entries: list[ContextEntry],
        action_contract: ActionContract,
    ) -> ModelTurn:
        options = dict(profile.request_options)
        for name in profile.unsupported_params:
            options.pop(name, None)
        effort = options.pop("effort", None)
        service_tier = options.pop("service_tier", None)
        if options:
            raise ValueError(f"{profile.model_name}: the Codex CLI cannot set "
                             f"{', '.join(sorted(options))}")

        home = Path(self._home.name)
        tool = canonical_tool(chat_format=True, width=observation.width,
                              height=observation.height, contract=action_contract)["function"]
        schema_path = home / "schema.json"
        schema_path.write_text(json.dumps(
            {**strict_schema(tool["parameters"]), "description": tool["description"]}))
        instructions = home / "instructions.md"
        instructions.write_text(system_prompt or "")
        answer = home / "answer.json"
        answer.unlink(missing_ok=True)

        texts: list[str] = []
        images: list[Path] = []
        for part in _openai_content(observation, context_entries,
                                    user_prompt="Current game screen:", image_detail="auto"):
            if part.get("type") == "image_url":
                path = home / f"image_{len(images) + 1}.png"
                path.write_bytes(base64.b64decode(str(part["image_url"]["url"]).partition(",")[2]))
                images.append(path)
                texts.append(f"[image {len(images)}]")
            else:
                texts.append(str(part.get("text") or ""))
        prompt = "\n".join(texts)
        if images:
            prompt = (f"{len(images)} images are attached in order; [image N] marks where "
                      f"the N-th belongs.\n\n{prompt}")

        command = [
            _codex(), "exec", "--json", "--ephemeral", "--skip-git-repo-check",
            "--ignore-user-config", "--ignore-rules", "-C", str(home),
            "-c", f"model_instructions_file={json.dumps(str(instructions))}",
            "--output-schema", str(schema_path), "-o", str(answer),
        ]
        if profile.model:
            command.extend(["-m", profile.model])
        command.extend(chatgpt_https_args(single_attempt=True))
        for key, value in PLAYER_CONTEXT_SETTINGS.items():
            command.extend(["-c", f"{key}={json.dumps(value)}"])
        if effort:
            command.extend(["-c", f"model_reasoning_effort={json.dumps(str(effort))}"])
        if service_tier:
            command.extend(["-c", f"service_tier={json.dumps(str(service_tier))}"])
        for feature in DISABLED_FEATURES:
            command.extend(["--disable", feature])
        for image in images:
            command.extend(["--image", str(image)])
        if self._pacer is not None:
            self._pacer.wait()
        try:
            events, returncode, stderr = run_cli(
                command, prompt=prompt, cwd=home, timeout=profile.request_timeout, label="Codex CLI",
                failed=lambda event: event.get("type") == "turn.failed")
        except RuntimeError as exc:
            if self._pacer is not None and (
                    re.search(r"\b429\b", str(exc)) or "turn_rate_limit_exceeded" in str(exc)):
                # CLI omits Retry-After. Our observed server window was 60 seconds;
                # wait a full window and leave a small margin, shared by all players.
                self._pacer.defer(65.0)
                logging.getLogger(__name__).warning("Codex rate limited; all players cooling down for 65s")
                raise ModelRateLimitError(str(exc), retry_after=65.0) from exc
            raise
        finished = next((event for event in reversed(events)
                         if event.get("type") == "turn.completed"), None)
        if returncode != 0 or finished is None or not answer.is_file():
            failure = next((event for event in reversed(events)
                            if event.get("type") in ("turn.failed", "error")), {})
            detail = (json.dumps(failure.get("error") or failure.get("message"))[:500] if failure
                      else stderr.strip()[-500:] or f"exit status {returncode}")
            raise RuntimeError(f"Codex CLI request failed: {detail}")

        reply: dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": []}
        try:
            arguments = json.loads(answer.read_text())
        except json.JSONDecodeError:
            arguments = None
        if isinstance(arguments, dict):
            reply["tool_calls"].append({"type": "function", "function": {
                "name": "computer_use",
                "arguments": json.dumps(_without_nulls(arguments), ensure_ascii=False)}})
        else:
            reply["content"] = answer.read_text()
        thinking = "\n".join(
            str(event["item"].get("text") or "")
            for event in events if event.get("type") == "item.completed"
            and isinstance(event.get("item"), dict) and event["item"].get("type") == "reasoning"
        ).strip()
        if thinking:
            reply["reasoning_content"] = thinking
        response = {
            "choices": [{"message": reply, "finish_reason": None}],
            "usage": _usage(finished.get("usage")),
            "codex_turn": finished,
        }
        actions, reasoning = parse_chat_tool_action(response)
        return ModelTurn(
            actions=actions,
            thinking=reasoning,
            request={"command": command, "prompt": prompt},
            response=response,
        )


def strict_schema(schema: Any) -> Any:
    """A schema as Codex's strict structured output takes it: every object lists all
    of its properties as required and allows no others, and a property that was
    optional may be null."""
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    if isinstance(out.get("properties"), dict):
        required = set(out.get("required") or [])
        properties = {}
        for name, child in out["properties"].items():
            child = strict_schema(child)
            properties[name] = child if name in required else _nullable(child)
        out["properties"] = properties
        out["required"] = list(properties)
        out["additionalProperties"] = False
    if isinstance(out.get("items"), dict):
        out["items"] = strict_schema(out["items"])
    if isinstance(out.get("anyOf"), list):
        out["anyOf"] = [strict_schema(branch) for branch in out["anyOf"]]
    return out


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    out = dict(schema)
    if isinstance(out.get("anyOf"), list):
        out["anyOf"] = [*out["anyOf"], {"type": "null"}]
        return out
    kind = out.get("type")
    if isinstance(kind, str):
        out["type"] = [kind, "null"]
    elif isinstance(kind, list) and "null" not in kind:
        out["type"] = [*kind, "null"]
    if isinstance(out.get("enum"), list) and None not in out["enum"]:
        out["enum"] = [*out["enum"], None]
    return out


def _without_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


def _codex() -> str:
    # Found where the codex coding agent's CLI is found ($GAMEENV_CODEX_BIN, PATH, the
    # VS Code extension), so both use one install.
    from tools.coding_session import load_agent

    return str(load_agent("codex").resolved_executable())


def _usage(usage: Any) -> dict[str, Any] | None:
    """Codex's token counts in the names the other backends report; Codex already
    counts cached input inside input and reasoning inside output."""
    if not isinstance(usage, dict):
        return None
    prompt = int(usage.get("input_tokens") or 0)
    completion = int(usage.get("output_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": int(usage.get("cached_input_tokens") or 0)},
        "completion_tokens_details": {"reasoning_tokens": int(usage.get("reasoning_output_tokens") or 0)},
    }


__all__ = ["CodexCliBackend", "strict_schema"]
