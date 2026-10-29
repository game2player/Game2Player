"""Persistent non-interactive Claude Code CLI session, on a Claude subscription.

Claude Code enforces an output schema itself (`--json-schema`): the answer arrives as
the `structured_output` of the final `result` event, so the schema is not appended to
the prompt. The silence budget, streaming collector, schema check and process-group
teardown are managed by this session.

Every run has its own Claude configuration directory, so a session never reads the
instructions, memory, settings, plugins or MCP servers of whoever's machine it runs on
(only the repository's own AGENTS.md), and resumes its conversation from there. That
directory holds no login: the session signs in with a long-lived subscription token
made once with `claude setup-token` and kept in a file private to its owner.

A turn that ends on the plan's usage limit raises `AgentUsageLimitError` with the
reset time read from the CLI's message, so a runner can hand the work back instead of
counting it as a failure.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import signal
import subprocess
import time
from collections import deque
from contextlib import nullcontext
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.codex_session import (
    USAGE_LIMIT_MARGIN_S,
    CodexProcessError,
    CodexProtocolError,
    AgentUsageLimitError,
    _workspace_environment,
)


# What a coding turn may use: a shell and the file tools. Subagents, scheduling and the
# rest of Claude Code's tools stay out, as they do for the other CLIs. A read-only turn
# has no editing tools.
TOOLS = ("Bash", "Read", "Edit", "Write")
READ_ONLY_TOOLS = ("Bash", "Read")
WEB_TOOLS = ("WebSearch", "WebFetch")
# "Claude AI usage limit reached|1760536800" (older CLIs: the reset as epoch seconds),
# "You've hit your session limit · resets 4:40am (UTC)", "Weekly limit reached ∙ resets
# Oct 20, 3pm". Without a readable time the work is tried again after the fallback.
_LIMIT_EPOCH = re.compile(r"usage limit reached\|(\d{9,})", re.I)
_LIMIT = re.compile(r"hit your (?:[\w-]+ )?limit|(?:usage|session|weekly|daily|5-hour|opus|sonnet) "
                    r"limit reached|out of (?:extra )?usage", re.I)
_LIMIT_RESET = re.compile(
    r"resets\s+(?:(?P<date>[A-Z][a-z]{2}\s+\d{1,2}),?\s+(?:at\s+)?)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d\d))?\s*(?P<half>am|pm)(?:\s*\((?P<zone>[^)]+)\))?", re.I)
LIMIT_FALLBACK_S = 30 * 60


def claude_limit_reset(text: str, now: datetime | None = None) -> float | None:
    """When a Claude plan limit named in `text` resets, as epoch seconds; None for none."""
    now = now or datetime.now().astimezone()
    match = _LIMIT_EPOCH.search(text)
    if match:
        return float(match[1]) + USAGE_LIMIT_MARGIN_S
    if not _LIMIT.search(text):
        return None
    match = _LIMIT_RESET.search(text)
    if match is None:
        return now.timestamp() + LIMIT_FALLBACK_S
    zone = now.tzinfo
    if match["zone"]:
        try:
            zone = ZoneInfo(match["zone"].strip())
        except (ZoneInfoNotFoundError, ValueError):
            pass
    local = now.astimezone(zone)
    hour = int(match["hour"]) % 12 + (12 if match["half"].lower() == "pm" else 0)
    reset = local.replace(hour=hour, minute=int(match["minute"] or 0), second=0, microsecond=0)
    if match["date"]:
        day = datetime.strptime(f"{' '.join(match['date'].split())} {local.year}", "%b %d %Y")
        reset = reset.replace(month=day.month, day=day.day)
        if reset < local - timedelta(days=180):
            reset = reset.replace(year=reset.year + 1)
    elif reset <= local - timedelta(hours=1):
        reset += timedelta(days=1)
    return max(reset.timestamp(), now.timestamp()) + USAGE_LIMIT_MARGIN_S


HARD_TURN_LIMIT_S = 4 * 60 * 60


class ClaudeSession:
    """Run structured turns in one resumable Claude Code session."""

    cli_name = "Claude Code"

    def __init__(
        self, *, root: Path, run_dir: Path, credential_file: Path,
        claude: str = "claude", thread_id: str | None = None, model: str | None = None,
        reasoning_effort: str = "high", turn_timeout_s: float = 90 * 60,
        full_access: bool = False, environment: Mapping[str, str] | None = None,
        on_thread_started: Callable[[str], None] | None = None,
        record_events: bool = True, web_search: bool = False,
    ):
        self.root = root.resolve()
        self.run_dir = run_dir.resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.thread_id = thread_id
        self.claude = claude
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.turn_timeout_s = turn_timeout_s
        self.full_access = full_access
        self.environment = environment
        self.on_thread_started = on_thread_started
        self.schema_path = self.run_dir / "result.schema.json"
        self.response_path = self.run_dir / "last_response.json"
        self.events_path = self.run_dir / "claude.jsonl" if record_events else None
        self.web_search = web_search
        self.last_output = ""
        self._result_text = None
        self.credential_file = Path(credential_file).expanduser()
        subscription_token(self.credential_file)
        self.config_dir = self.run_dir / "claude_home"

    @property
    def environment(self) -> dict[str, str] | None:
        # Resolved on read rather than stored, because callers assign to this
        # attribute after construction and would otherwise drop the isolation.
        return claude_environment(self._environment, config_dir=self.config_dir,
                                  token=subscription_token(self.credential_file))

    @environment.setter
    def environment(self, value: Mapping[str, str] | None) -> None:
        self._environment = dict(value) if value is not None else None

    def turn(self, prompt: str, schema: Mapping[str, Any], *,
             sandbox: str = "workspace-write") -> dict[str, Any]:
        self._error_result = ""
        try:
            return self._turn(prompt, schema, sandbox=sandbox)
        except (CodexProcessError, CodexProtocolError) as exc:
            # Only the CLI's own error result is read: the exception also carries the
            # last lines of the stream, tool output included, which may say anything.
            resets = claude_limit_reset(self._error_result) if self._error_result else None
            if resets is None:
                raise
            raise AgentUsageLimitError(f"{exc} {self._error_result}", resets) from exc

    def _turn(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        sandbox: str = "workspace-write",
    ) -> dict[str, Any]:
        self.schema_path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.response_path.unlink(missing_ok=True)
        self._result_text = None
        process = subprocess.Popen(
            self.command(prompt=prompt, sandbox=sandbox),
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=_workspace_environment(self.root, self.environment),
        )
        assert process.stdout is not None
        try:
            self._collect(process)
        except BaseException:
            self._stop(process)
            raise
        finally:
            try:
                process.stdout.close()
            except OSError:
                pass

        if process.returncode != 0:
            detail = f": {self.last_output}" if self.last_output else ""
            raise CodexProcessError(
                f"{self.cli_name} exited with status {process.returncode}{detail}"
            )
        if not self.thread_id:
            raise CodexProtocolError(f"{self.cli_name} did not report a session id")
        if self._result_text is None:
            raise CodexProtocolError(f"{self.cli_name} did not return a final result")
        result = json.loads(self._result_text)
        _validate_schema(result, schema)
        self.response_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summary = result.get("summary")
        if isinstance(summary, str) and summary.strip():
            phase = result.get("phase", "progress")
            status = result.get("status", "update")
            print(f"[{self.cli_name}] {phase}/{status}: {summary.strip()}", flush=True)
        return result

    def _collect(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        # Productive output refreshes the silence budget; a hard limit also
        # stops a CLI that streams forever without finishing.
        deadline = time.monotonic() + self.turn_timeout_s
        hard_deadline = time.monotonic() + HARD_TURN_LIMIT_S
        drain_deadline: float | None = None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        recent: deque[str] = deque(maxlen=8)
        pending = bytearray()

        def handle(raw_line: bytes, events: Any) -> None:
            nonlocal deadline
            deadline = time.monotonic() + self.turn_timeout_s
            line = raw_line.decode("utf-8", errors="replace")
            recent.append(line.strip()[-2000:])
            if events is not None:
                events.write(line)
                events.flush()
            self._handle_event(line)

        try:
            event_stream = (
                self.events_path.open("a", encoding="utf-8")
                if self.events_path is not None
                else nullcontext(None)
            )
            with event_stream as events:
                stdout_open = True
                while process.poll() is None or stdout_open:
                    now = time.monotonic()
                    if now >= hard_deadline:
                        self._stop(process)
                        raise TimeoutError(
                            f"{self.cli_name} turn exceeded the "
                            f"{HARD_TURN_LIMIT_S / 3600:.0f}h hard limit"
                        )
                    if now >= deadline:
                        self._stop(process)
                        raise TimeoutError(
                            f"{self.cli_name} turn was silent for "
                            f"{self.turn_timeout_s / 60:.0f} minutes"
                        )
                    if process.returncode is not None and drain_deadline is None:
                        drain_deadline = now + 3.0
                    if drain_deadline is not None and now >= drain_deadline:
                        self._stop_process_group(process)
                        break
                    if not stdout_open:
                        time.sleep(0.05)
                        continue
                    ready = selector.select(timeout=min(1.0, max(0.0, deadline - now)))
                    if not ready:
                        continue
                    chunk = os.read(process.stdout.fileno(), 64 * 1024)
                    if not chunk:
                        stdout_open = False
                        selector.unregister(process.stdout)
                        continue
                    pending.extend(chunk)
                    while True:
                        newline = pending.find(b"\n")
                        if newline < 0:
                            break
                        raw_line = bytes(pending[: newline + 1])
                        del pending[: newline + 1]
                        handle(raw_line, events)
                if pending:
                    handle(bytes(pending), events)
        except BaseException:
            self._stop(process)
            raise
        finally:
            selector.close()
            self.last_output = " | ".join(part for part in recent if part)

    @staticmethod
    def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        if process.poll() is None:
            try:
                process.wait(timeout=10)
                return
            except subprocess.TimeoutExpired:
                pass
        else:
            time.sleep(0.1)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    @staticmethod
    def _stop(process: subprocess.Popen[bytes]) -> None:
        ClaudeSession._stop_process_group(process)

    def command(self, *, prompt: str, sandbox: str) -> list[str]:
        writes = self.full_access or sandbox == "workspace-write"
        tools = (TOOLS if writes else READ_ONLY_TOOLS) + (WEB_TOOLS if self.web_search else ())
        command = [
            self.claude, "--print", "--output-format", "stream-json", "--verbose",
            "--json-schema", self.schema_path.read_text(encoding="utf-8"),
            "--tools", ",".join(tools), "--strict-mcp-config",
        ]
        if self.model:
            command.extend(["--model", self.model])
        if self.reasoning_effort:
            command.extend(["--effort", self.reasoning_effort])
        if writes:
            command.extend(["--permission-mode", "bypassPermissions"])
        else:
            # Claude Code has no read-only sandbox. A read-only turn may still run
            # commands (fetch and try a game in the role's scratch directory): Claude
            # Code's auto mode refuses the risky ones, nothing waits for approval, and
            # the role's prompt keeps it out of the repository. The mode is named
            # rather than inherited, since an account's default differs.
            command.extend(["--permission-mode", "auto", "--permission-prompts", "none"])
            if self.web_search:
                command.extend(["--allowed-tools", ",".join(WEB_TOOLS)])
        if self.thread_id:
            command.extend(["--resume", self.thread_id])
        command.extend(["--", prompt])
        return command

    def _handle_event(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id and not self.thread_id:
            self.thread_id = session_id
            if self.on_thread_started:
                self.on_thread_started(session_id)
        if event.get("type") == "result" and event.get("is_error"):
            self._error_result = str(event.get("result") or event.get("subtype") or "")
        if event.get("type") != "result" or event.get("subtype") != "success":
            return
        answer = event.get("structured_output")
        if isinstance(answer, dict):
            self._result_text = json.dumps(answer, ensure_ascii=False)


def subscription_token(path: Path) -> str:
    """The subscription token in a file readable only by its owner."""
    path = Path(path).expanduser()
    hint = f"run `claude setup-token` and save the token it prints to {path} (mode 0600)"
    if not path.is_file():
        raise ValueError(f"Claude token file not found: {hint}")
    if path.stat().st_mode & 0o077:
        raise ValueError(f"Claude token file must be readable only by its owner: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"Claude token file is empty: {hint}")
    return token


def claude_environment(
    base: Mapping[str, str] | None, *, config_dir: Path, token: str
) -> dict[str, str]:
    """A Claude Code process environment that depends on nothing of the machine's own
    Claude setup: its own configuration directory and the subscription token.

    Every inherited CLAUDE*/ANTHROPIC* variable is dropped. An API key would bill the
    API instead of the subscription, and a launching Claude Code session exports its
    own settings (effort, session links) that would otherwise override this run's.
    """
    environment = {
        name: value for name, value in (os.environ if base is None else base).items()
        if not name.startswith(("CLAUDE", "ANTHROPIC"))
    }
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment["CLAUDE_CONFIG_DIR"] = str(config_dir)
    environment["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return environment


def claude_installs() -> list[str]:
    """Where Claude Code is installed when it is not on PATH: the native installer's
    launcher, then the VS Code extension's binary, newest first."""
    home = Path.home()
    extensions = home / ".vscode-server" / "extensions"
    return [str(home / ".local" / "bin" / "claude"), str(home / ".claude" / "local" / "claude")] + [
        str(path) for path in sorted(
            extensions.glob("anthropic.claude-code-*/resources/native-binary/claude"), reverse=True)]


def claude_usage(events_path: Path) -> dict[str, int] | None:
    """Token totals of a Claude Code event log in the keys Codex reports, or None.

    Each turn's final `result` event counts every model call of that turn in `usage`
    (its `modelUsage` is the running total of the whole session, resumed turns
    included, so it is not added up); a turn stopped before its result is not counted.
    Claude counts cached and newly cached input apart from `input_tokens`, and thinking
    inside output; Codex counts all input in one number, which is what this returns.
    """
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    totals = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
              "reasoning_output_tokens": 0}
    turns = 0
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "result":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        details = usage.get("output_tokens_details")
        cached = _count(usage.get("cache_read_input_tokens"))
        totals["input_tokens"] += (_count(usage.get("input_tokens")) + cached
                                   + _count(usage.get("cache_creation_input_tokens")))
        totals["cached_input_tokens"] += cached
        totals["output_tokens"] += _count(usage.get("output_tokens"))
        totals["reasoning_output_tokens"] += _count(
            details.get("thinking_tokens") if isinstance(details, dict) else None)
        turns += 1
    return totals if turns else None


def _count(value: Any) -> int:
    return value if isinstance(value, int) else 0


__all__ = ["ClaudeSession", "claude_environment", "claude_installs", "claude_usage",
           "subscription_token"]


def _validate_schema(value: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
    }
    if isinstance(expected, str) and expected in matches and not matches[expected]:
        raise CodexProtocolError(f"Claude result {path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise CodexProtocolError(f"Claude result {path} has an unsupported value")
    if isinstance(value, dict):
        required = schema.get("required") or []
        missing = [name for name in required if name not in value]
        if missing:
            raise CodexProtocolError(
                f"Claude result {path} is missing: {', '.join(missing)}"
            )
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise CodexProtocolError(
                    f"Claude result {path} has unexpected fields: {', '.join(extra)}"
                )
        for name, child in properties.items():
            if name in value and isinstance(child, Mapping):
                _validate_schema(value[name], child, f"{path}.{name}")
    if isinstance(value, list):
        child = schema.get("items")
        if isinstance(child, Mapping):
            for index, item in enumerate(value):
                _validate_schema(item, child, f"{path}[{index}]")
