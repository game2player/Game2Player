"""Persistent non-interactive Codex session used by repository workflows."""

from __future__ import annotations

import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tools.codex_transport import chatgpt_https_args


HARD_TURN_LIMIT_S = 4 * 60 * 60
# After a usage limit, the turn is retried this long after the reset time Codex names,
# or this long from now when it names none or one that has just passed.
USAGE_LIMIT_MARGIN_S = 60
USAGE_LIMIT_FALLBACK_S = 15 * 60
USAGE_CHECK_TIMEOUT_S = 180
_sleep = time.sleep  # how a session waits out a limit; tests replace it
_USAGE_LIMIT = re.compile(r"hit your usage limit|usage_limit_exceeded", re.I)
# "try again at 2:26 AM" for the short window, "try again at Oct 13th, 2026 2:14 AM"
# for the weekly one; Codex prints the machine's local time.
_RESET_AT = re.compile(
    r"try again at\s+(?:(?P<date>[A-Z][a-z]+\.?\s+\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>\d{4})\s+)?"
    r"(?P<time>\d{1,2}:\d{2}\s*[AP]M)", re.I)


def _workspace_environment(root: Path, environment: Mapping[str, str] | None) -> dict[str, str]:
    """Use this checkout and the runner's Python for agent-generated scripts."""
    result = dict(os.environ if environment is None else environment)
    result["PYTHONPATH"] = str(root.resolve())
    python_bin = str(Path(sys.executable).parent)
    result["PATH"] = os.pathsep.join(filter(None, (python_bin, result.get("PATH", ""))))
    return result


def logged_usage(events_path: Path) -> dict[str, int] | None:
    """Token totals of every thread in a Codex event log, or None without a report.

    Codex reports each completed turn with the thread's running total, including
    turns of earlier processes that resumed it, so a thread costs its last report.
    """
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    threads: dict[str | None, dict[str, int]] = {}
    thread = None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            thread = event.get("thread_id")
        elif event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            threads[thread] = {key: value for key, value in event["usage"].items()
                               if isinstance(value, int)}
    if not threads:
        return None
    totals: dict[str, int] = {}
    for usage in threads.values():
        for key, value in usage.items():
            totals[key] = totals.get(key, 0) + value
    return totals


class CodexSessionError(RuntimeError):
    """Base class for failures reported by the non-interactive Codex runner."""


class CodexProcessError(CodexSessionError):
    """The Codex process or transport failed."""


class CodexProtocolError(CodexSessionError):
    """Codex exited normally but did not produce the required structured result."""


class AgentUsageLimitError(CodexProcessError):
    """The account's usage limit was reached; it resets at `resets_at` (epoch s)."""

    def __init__(self, message: str, resets_at: float):
        super().__init__(message)
        self.resets_at = resets_at


def usage_limit_reset(text: str, now: datetime | None = None) -> float | None:
    """When a Codex usage limit in `text` resets, as epoch seconds; None for no limit."""
    if not _USAGE_LIMIT.search(text):
        return None
    now = now or datetime.now().astimezone()
    match = _RESET_AT.search(text)
    if match is None:
        return now.timestamp() + USAGE_LIMIT_FALLBACK_S
    clock = datetime.strptime(re.sub(r"\s+", " ", match["time"].upper()), "%I:%M %p")
    if match["date"]:
        day = datetime.strptime(f"{match['date'].replace('.', '')} {match['year']}", "%b %d %Y")
        reset = now.replace(year=day.year, month=day.month, day=day.day,
                            hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    else:
        reset = now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
        # A time without a date is the next one; one that has only just passed is now.
        if reset <= now - timedelta(hours=1):
            reset += timedelta(days=1)
    return max(reset.timestamp(), now.timestamp()) + USAGE_LIMIT_MARGIN_S


class CodexSession:
    """Run structured turns in one resumable Codex CLI thread."""

    def __init__(
        self,
        *,
        root: Path,
        run_dir: Path,
        thread_id: str | None = None,
        codex: str = "codex",
        model: str | None = None,
        reasoning_effort: str = "high",
        turn_timeout_s: float = 90 * 60,
        full_access: bool = False,
        environment: Mapping[str, str] | None = None,
        on_thread_started: Callable[[str], None] | None = None,
        record_events: bool = True,
        web_search: bool = False,
        service_tier: str | None = None,
        wait_for_usage_limit: bool | None = None,
    ):
        self.root = root.resolve()
        self.run_dir = run_dir.resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.thread_id = thread_id
        self.codex = codex
        self.model = model
        self.reasoning_effort = reasoning_effort
        # Pin ordinary speed unless the agent profile explicitly requests another
        # tier; the IDE writes its speed preference to the shared user config.
        self.service_tier = service_tier or "default"
        self.turn_timeout_s = turn_timeout_s
        self.full_access = full_access
        self.environment = dict(environment) if environment is not None else None
        self.on_thread_started = on_thread_started
        self.schema_path = self.run_dir / "result.schema.json"
        self.response_path = self.run_dir / "last_response.json"
        self.events_path = self.run_dir / "codex.jsonl" if record_events else None
        self.web_search = web_search
        # A turn that hits the account's usage limit sleeps until the limit resets and
        # runs again on the same thread, instead of failing the workflow that asked.
        # GAMEENV_CODEX_WAIT_FOR_LIMIT=0 turns the wait off by default, for a runner
        # that gives the work to another agent instead.
        if wait_for_usage_limit is None:
            wait_for_usage_limit = os.environ.get("GAMEENV_CODEX_WAIT_FOR_LIMIT") != "0"
        self.wait_for_usage_limit = wait_for_usage_limit
        self.last_output = ""

    def turn(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        sandbox: str = "workspace-write",
    ) -> dict[str, Any]:
        while True:
            try:
                return self._turn(prompt, schema, sandbox=sandbox)
            except AgentUsageLimitError as exc:
                if not self.wait_for_usage_limit:
                    raise
                self._wait_out_usage_limit(exc.resets_at)

    def _wait_out_usage_limit(self, resets_at: float) -> None:
        """Sleep until the reset, then return once a check finds the limit gone."""
        while True:
            resets = datetime.fromtimestamp(resets_at).astimezone()
            print(f"[codex] usage limit reached; waiting until it resets at "
                  f"{resets:%Y-%m-%d %H:%M %Z}", flush=True)
            _sleep(max(0.0, resets_at - time.time()))
            still = self.usage_limit_check()
            if still is None:
                return
            resets_at = still

    def usage_limit_check(self) -> float | None:
        """When the account's usage limit resets, from a one-line throwaway call with
        this session's model and environment; None when it is not limited. A check that
        fails any other way also returns None, and the turn itself then reports why."""
        command = [self.codex, "exec", "--json", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "-C", str(self.run_dir),
                   "-c", 'model_reasoning_effort="low"']
        if self.model:
            command.extend(["--model", self.model])
        if self.service_tier:
            command.extend(["-c", f"service_tier={json.dumps(self.service_tier)}"])
        if not (self.environment or {}).get("GAMEENV_LOCAL_PROXY_AUTH"):
            command.extend(chatgpt_https_args())
        command.append("-")
        try:
            done = subprocess.run(command, input=b"Reply with the single word OK.",
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  env=_workspace_environment(self.root, self.environment),
                                  timeout=USAGE_CHECK_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode == 0:
            return None
        return usage_limit_reset(done.stdout.decode("utf-8", errors="replace"))

    def _turn(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        sandbox: str,
    ) -> dict[str, Any]:
        self.schema_path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.response_path.unlink(missing_ok=True)
        command = self.command(sandbox=sandbox)
        process = subprocess.Popen(
            command,
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=_workspace_environment(self.root, self.environment),
        )
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
            self._collect(process)
        except BaseException:
            self._stop(process)
            raise
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.stdout.close()
            except OSError:
                pass

        if process.returncode != 0:
            detail = f": {self.last_output}" if self.last_output else ""
            message = f"codex exited with status {process.returncode}{detail}"
            resets_at = usage_limit_reset(self.last_output)
            if resets_at is not None:
                raise AgentUsageLimitError(message, resets_at)
            raise CodexProcessError(message)
        if not self.thread_id:
            raise CodexProtocolError("Codex JSON stream did not include a thread id")
        if not self.response_path.is_file():
            raise CodexProtocolError("Codex did not write its structured final response")
        try:
            result = json.loads(self.response_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CodexProtocolError(
                f"Codex final response was not valid JSON: {exc}"
            ) from exc
        if not isinstance(result, dict):
            raise CodexProtocolError("Codex final response must be a JSON object")
        return result

    def command(self, *, sandbox: str) -> list[str]:
        command = [self.codex]
        if self.web_search:
            command.append("--search")
        command.extend([
            "exec",
            "--json",
            "--output-schema",
            str(self.schema_path),
            "--output-last-message",
            str(self.response_path),
            "-C",
            str(self.root),
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
        ])
        command.extend(["-c", f"service_tier={json.dumps(self.service_tier)}"])
        command.extend(chatgpt_https_args())
        # Codex filters the environment again before executing shell tools.
        environment = _workspace_environment(self.root, self.environment)
        for key in ("PYTHONPATH", "PATH"):
            command.extend(["-c", f"shell_environment_policy.set.{key}={json.dumps(environment[key])}"])
        if self.model:
            command.extend(["--model", self.model])
        if self.full_access:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        elif sandbox == "workspace-write":
            command.append("--approve-for-me")
        else:
            command.extend(["--sandbox", sandbox])
        if self.thread_id:
            command.extend(["resume", self.thread_id, "-"])
        else:
            command.append("-")
        return command

    def _collect(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        # The budget is time spent *silent*, not time spent working. It used to be an
        # absolute deadline set once per turn, which could not tell a turn that was
        # streaming events every 50ms for forty minutes from one that had hung: both
        # were killed the same way. Measured over 39412 real gaps in this repository,
        # the median is 0.05s and the longest genuine in-turn silence is about ten
        # minutes (one duration-capped episode plus its analysis), so a silence budget
        # both lets a long productive turn finish and notices a hang sooner than the
        # old wall clock did. HARD_TURN_LIMIT_S is the backstop for the case a silence
        # budget cannot see: a turn that keeps emitting events but never converges.
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
                            f"Codex turn exceeded the "
                            f"{HARD_TURN_LIMIT_S / 3600:.0f}h hard limit"
                        )
                    if now >= deadline:
                        self._stop(process)
                        raise TimeoutError(
                            "Codex turn was silent for "
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

    def _handle_event(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if event.get("type") != "thread.started":
            item = event.get("item")
            if event.get("type") != "item.completed" or not isinstance(item, dict):
                return
            if item.get("type") != "agent_message":
                return
            message = item.get("text")
            if not isinstance(message, str) or not message.strip():
                return
            stripped = message.strip()
            if stripped.startswith("{"):
                try:
                    structured = json.loads(stripped)
                except json.JSONDecodeError:
                    return
                if not isinstance(structured, dict):
                    return
                summary = structured.get("summary")
                if isinstance(summary, str) and summary.strip():
                    phase = structured.get("phase", "progress")
                    status = structured.get("status", "update")
                    print(f"[codex] {phase}/{status}: {summary.strip()}", flush=True)
                return
            print(f"[codex] {stripped}", flush=True)
            return
        thread_id = event.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id or self.thread_id:
            return
        self.thread_id = thread_id
        if self.on_thread_started:
            self.on_thread_started(thread_id)

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
        CodexSession._stop_process_group(process)


__all__ = [
    "CodexProcessError",
    "CodexProtocolError",
    "CodexSession",
    "CodexSessionError",
    "AgentUsageLimitError",
    "logged_usage",
    "usage_limit_reset",
]
