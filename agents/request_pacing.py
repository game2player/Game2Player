"""A shared request clock for separate Codex player processes."""

from __future__ import annotations

import os
from pathlib import Path
import time

from filelock import FileLock


class RequestPacer:
    def __init__(self, path: Path, interval_s: float):
        if interval_s <= 0:
            raise ValueError("request interval must be positive")
        self.path = path
        self.interval_s = interval_s
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(path) + ".lock")

    @classmethod
    def from_environment(cls) -> RequestPacer | None:
        path = os.environ.get("GAME2PLAYER_CODEX_RATE_STATE")
        interval = os.environ.get("GAME2PLAYER_CODEX_REQUEST_INTERVAL_S")
        if not path and not interval:
            return None
        if not path or not interval:
            raise ValueError("Codex request pacing needs both a state path and an interval")
        return cls(Path(path), float(interval))

    def _next_at(self) -> float:
        return float(self.path.read_text()) if self.path.exists() else 0.0

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.time()
                delay = self._next_at() - now
                if delay <= 0:
                    self.path.write_text(str(now + self.interval_s))
                    return
            # Re-read after sleeping: another player may have extended the cooldown.
            time.sleep(delay)

    def defer(self, seconds: float) -> None:
        with self.lock:
            self.path.write_text(str(max(self._next_at(), time.time() + seconds)))
