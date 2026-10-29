"""Read a model CLI's output while it runs, including errors before it exits."""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tools.paths import temporary_dir


def run_cli(
    command: list[str], *, prompt: str, cwd: Path, timeout: float, label: str,
    failed: Callable[[dict[str, Any]], bool],
    environment: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], int, str]:
    """Return JSONL events, exit code and stderr; terminal failures return early.

    Pipe readers run separately so both streams are drained on Windows as well as
    Unix. Stdin is a file: a large image-bearing prompt must not block reading the
    child's output or starting the request timeout.
    """
    logger = logging.getLogger(__name__)
    output: queue.Queue[tuple[str, bytes | None]] = queue.Queue()
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    stderr: list[str] = []

    def read(name: str, pipe) -> None:
        try:
            for line in iter(pipe.readline, b""):
                output.put((name, line))
        finally:
            output.put((name, None))

    def detail() -> str:
        return "\n".join([*errors[-3:], "".join(stderr)[-2000:]]).strip()

    with tempfile.TemporaryFile(dir=temporary_dir("cli")) as stdin:
        stdin.write(prompt.encode("utf-8"))
        stdin.seek(0)
        process = subprocess.Popen(command, stdin=stdin, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=cwd, env=environment)
        readers = [threading.Thread(target=read, args=(name, pipe), daemon=True)
                   for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        closed = 0
        try:
            while closed < 2:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"{label} did not finish within {timeout:.0f}s; "
                                       f"output received before timeout:\n{detail() or '(none)'}")
                try:
                    name, raw = output.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    continue
                if raw is None:
                    closed += 1
                    continue
                line = raw.decode("utf-8", errors="replace")
                if name == "stderr":
                    stderr.append(line)
                    logger.warning("%s stderr: %s", label, line.rstrip())
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                events.append(event)
                if event.get("type") in {"error", "turn.failed"} or failed(event):
                    errors.append(line.rstrip())
                    logger.warning("%s event: %s", label, line.rstrip())
                if failed(event):
                    message = event.get("error") or event.get("result") or event.get("message") or event
                    raise RuntimeError(f"{label} request failed: {json.dumps(message, ensure_ascii=False)}")
            try:
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"{label} did not finish within {timeout:.0f}s; "
                                   f"output received before timeout:\n{detail() or '(none)'}") from exc
            return events, code, "".join(stderr)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for reader in readers:
                reader.join(timeout=1)
            process.stdout.close()
            process.stderr.close()


__all__ = ["run_cli"]
