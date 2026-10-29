"""Shared browser, video, input, state, and task recording lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.async_api import Error as PlaywrightError

from catalog.games import load_game, resolve_game_id
from catalog.tasks import load_task
from env import BrowserConfig, BrowserGameManager, GameLauncher, build_task_evaluator
from env.browser_manager import GameInterfaceError, RESET_TIMEOUT_S
from env.game_launcher import append_url_suffix
from env.game_state_tracker import FOCUS_PAGE_SCRIPT, build_game_state_tracker
from env.task_evaluator import reset_task_evaluator_episode_metrics
from tools.paths import temporary_dir
from tools.unity_webgl.profile import load_profile_for_game
from tools.trajectory_schema import validate_episode
from tools.rollout.env_reports import system_report_path
from tools.trajectory_recorder.video_timing import (
    calibrate_video_offset,
    show_sync_marker,
    trim_video_to_trajectory_start,
)

LOGGER = logging.getLogger(__name__)

PAGE_CLOCK_SCRIPT = """
() => {
    const perfMs = window.__realPerfNow ? window.__realPerfNow() : performance.now();
    const epochMs = window.__realDateNow
        ? window.__realDateNow()
        : performance.timeOrigin + perfMs;
    return {
        perf_ms: perfMs,
        epoch_ms: epochMs,
        time_origin_ms: performance.timeOrigin,
        viewport: {
            width: window.innerWidth,
            height: window.innerHeight,
            outer_width: window.outerWidth,
            outer_height: window.outerHeight,
            device_pixel_ratio: window.devicePixelRatio || 1,
            scroll_x: window.scrollX || 0,
            scroll_y: window.scrollY || 0
        },
        pointer_locked: !!document.pointerLockElement
    };
}
"""

INSTALL_INPUT_RECORDER_SCRIPT = """
(config) => {
    if (window.__inputRecorder && window.__inputRecorder.dispose) {
        window.__inputRecorder.dispose();
    }

    const events = [];
    const handlers = [];
    let sequence = 0;
    let lastMouseMovePerfMs = -Infinity;
    let lastPointerMovePerfMs = -Infinity;
    const recordRawMousemove = config.recordRawMousemove !== false;
    const mousemoveMinIntervalMs = Math.max(0, Number(config.mousemoveMinIntervalMs || 0));
    const pressedKeys = new Set();
    const mouse = {
        client_x: null,
        client_y: null,
        page_x: null,
        page_y: null,
        screen_x: null,
        screen_y: null,
        buttons: 0,
        movement_x: 0,
        movement_y: 0
    };

    function finiteOrNull(value) {
        return Number.isFinite(value) ? value : null;
    }

    function rounded(value) {
        return Number.isFinite(value) ? Number(value.toFixed(6)) : null;
    }

    function modifiers(event) {
        const items = [];
        if (event.altKey) items.push("Alt");
        if (event.ctrlKey) items.push("Control");
        if (event.metaKey) items.push("Meta");
        if (event.shiftKey) items.push("Shift");
        return items;
    }

    function targetInfo(event) {
        const target = event.target;
        if (!target || !target.tagName) return null;
        return {
            tag: String(target.tagName).toLowerCase(),
            id: target.id || null,
            class_name: typeof target.className === "string" ? target.className : null
        };
    }

    function canvasInfo(event) {
        const clientX = finiteOrNull(event.clientX);
        const clientY = finiteOrNull(event.clientY);
        const target = event.target;
        const canvas = target instanceof HTMLCanvasElement ? target : document.querySelector("canvas");
        if (!canvas) return null;

        const rect = canvas.getBoundingClientRect();
        const width = rect.width || 0;
        const height = rect.height || 0;
        const x = clientX === null ? null : clientX - rect.left;
        const y = clientY === null ? null : clientY - rect.top;
        return {
            left: rounded(rect.left),
            top: rounded(rect.top),
            width: rounded(width),
            height: rounded(height),
            x: rounded(x),
            y: rounded(y),
            x_norm: x === null || width <= 0 ? null : rounded(x / width),
            y_norm: y === null || height <= 0 ? null : rounded(y / height)
        };
    }

    function viewportInfo() {
        return {
            width: window.innerWidth,
            height: window.innerHeight,
            device_pixel_ratio: window.devicePixelRatio || 1,
            scroll_x: window.scrollX || 0,
            scroll_y: window.scrollY || 0
        };
    }

    function buildRecord(event, type) {
        const perfMs = window.__realPerfNow ? window.__realPerfNow() : performance.now();
        const epochMs = window.__realDateNow
            ? window.__realDateNow()
            : performance.timeOrigin + perfMs;
        return {
            sequence: sequence++,
            t_perf_ms: perfMs,
            t_epoch_ms: epochMs,
            type,
            is_trusted: event.isTrusted === true,
            pointer_type: typeof event.pointerType === "string" ? event.pointerType : null,
            key: event.key || null,
            code: event.code || null,
            repeat: event.repeat === true,
            button: Number.isFinite(event.button) ? event.button : null,
            buttons: Number.isFinite(event.buttons) ? event.buttons : null,
            client_x: finiteOrNull(event.clientX),
            client_y: finiteOrNull(event.clientY),
            page_x: finiteOrNull(event.pageX),
            page_y: finiteOrNull(event.pageY),
            screen_x: finiteOrNull(event.screenX),
            screen_y: finiteOrNull(event.screenY),
            movement_x: finiteOrNull(event.movementX),
            movement_y: finiteOrNull(event.movementY),
            delta_x: finiteOrNull(event.deltaX),
            delta_y: finiteOrNull(event.deltaY),
            delta_z: finiteOrNull(event.deltaZ),
            delta_mode: Number.isFinite(event.deltaMode) ? event.deltaMode : null,
            modifiers: modifiers(event),
            keys: Array.from(pressedKeys).sort(),
            pointer_locked: !!document.pointerLockElement,
            viewport: viewportInfo(),
            canvas: canvasInfo(event),
            target: targetInfo(event)
        };
    }

    function updateState(event, type) {
        if (type === "keydown") pressedKeys.add(event.code || event.key || "key");
        if (type === "keyup") pressedKeys.delete(event.code || event.key || "key");
        if (
            type === "mousedown" ||
            type === "mouseup" ||
            type === "mousemove" ||
            type === "pointerdown" ||
            type === "pointerup" ||
            type === "pointermove" ||
            type === "click" ||
            type === "dblclick" ||
            type === "contextmenu"
        ) {
            mouse.client_x = finiteOrNull(event.clientX);
            mouse.client_y = finiteOrNull(event.clientY);
            mouse.page_x = finiteOrNull(event.pageX);
            mouse.page_y = finiteOrNull(event.pageY);
            mouse.screen_x = finiteOrNull(event.screenX);
            mouse.screen_y = finiteOrNull(event.screenY);
            mouse.buttons = Number.isFinite(event.buttons) ? event.buttons : mouse.buttons;
            mouse.movement_x += Number.isFinite(event.movementX) ? event.movementX : 0;
            mouse.movement_y += Number.isFinite(event.movementY) ? event.movementY : 0;
        }
    }

    function reset(reason) {
        pressedKeys.clear();
        mouse.buttons = 0;
        mouse.movement_x = 0;
        mouse.movement_y = 0;
        events.push({
            sequence: sequence++,
            t_perf_ms: performance.now(),
            t_epoch_ms: performance.timeOrigin + performance.now(),
            type: "input_reset",
            reason,
            keys: [],
            buttons: 0,
            pointer_locked: !!document.pointerLockElement,
            viewport: viewportInfo()
        });
    }

    function handle(type) {
        return (event) => {
            updateState(event, type);
            if (type === "mousemove") {
                if (!recordRawMousemove) return;
                const now = performance.now();
                if (now - lastMouseMovePerfMs < mousemoveMinIntervalMs) return;
                lastMouseMovePerfMs = now;
            }
            if (type === "pointermove") {
                // Only while a button is held: the press's path, which a page that
                // cancels pointerdown hides from mousemove. Hover stays in mousemove.
                if (!recordRawMousemove || !event.buttons) return;
                const now = performance.now();
                if (now - lastPointerMovePerfMs < mousemoveMinIntervalMs) return;
                lastPointerMovePerfMs = now;
            }
            events.push(buildRecord(event, type));
        };
    }

    // Pointer events too: a page can cancel pointerdown, after which the browser sends
    // it no mousedown or mouseup for that press, but the pointer events still arrive.
    const eventTypes = [
        "keydown",
        "keyup",
        "mousedown",
        "mouseup",
        "mousemove",
        "pointerdown",
        "pointerup",
        "pointermove",
        "wheel",
        "click",
        "dblclick",
        "contextmenu"
    ];

    for (const type of eventTypes) {
        const listener = handle(type);
        window.addEventListener(type, listener, { capture: true, passive: true });
        handlers.push([window, type, listener]);
    }

    // Capturing on window also sees every element's blur; only the window's own is
    // focus leaving the page.
    const blurListener = (event) => {
        if (event.target === window) reset("window_blur");
    };
    window.addEventListener("blur", blurListener, {capture: true});
    handlers.push([window, "blur", blurListener]);

    const visibilityListener = () => {
        events.push({
            sequence: sequence++,
            t_perf_ms: performance.now(),
            type: "visibilitychange",
            hidden: document.hidden,
            visibility_state: document.visibilityState,
            keys: Array.from(pressedKeys).sort(),
            pointer_locked: !!document.pointerLockElement,
            viewport: viewportInfo()
        });
        if (document.hidden) reset("document_hidden");
    };
    document.addEventListener("visibilitychange", visibilityListener, {capture: true});
    handlers.push([document, "visibilitychange", visibilityListener]);

    const pointerLockListener = () => events.push({
        sequence: sequence++,
        t_perf_ms: performance.now(),
        type: "pointerlockchange",
        pointer_locked: !!document.pointerLockElement,
        keys: Array.from(pressedKeys).sort(),
        viewport: viewportInfo()
    });
    document.addEventListener("pointerlockchange", pointerLockListener, {capture: true});
    handlers.push([document, "pointerlockchange", pointerLockListener]);

    reset("recording_start");

    window.__inputRecorder = {
        installed_at_perf_ms: performance.now(),
        drain() {
            return events.splice(0, events.length);
        },
        reset,
        size() {
            return events.length;
        },
        dispose() {
            for (const [target, type, listener] of handlers) {
                target.removeEventListener(type, listener, { capture: true });
            }
            handlers.length = 0;
            events.length = 0;
        }
    };

    return {
        installed_at_perf_ms: window.__inputRecorder.installed_at_perf_ms,
        event_types: eventTypes
    };
}
"""

DRAIN_INPUT_RECORDER_SCRIPT = """
() => {
    const recorder = window.__inputRecorder;
    if (!recorder || typeof recorder.drain !== "function") {
        throw new Error("Input recorder is not installed.");
    }
    return recorder.drain();
}
"""

RESET_INPUT_RECORDER_SCRIPT = """
(reason) => {
    const recorder = window.__inputRecorder;
    if (!recorder || typeof recorder.reset !== "function") {
        throw new Error("Input recorder is not installed.");
    }
    recorder.reset(String(reason || "manual"));
}
"""

DISPOSE_INPUT_RECORDER_SCRIPT = """
() => {
    const recorder = window.__inputRecorder;
    if (recorder && typeof recorder.dispose === "function") {
        recorder.reset("recording_end");
        const finalEvents = recorder.drain();
        recorder.dispose();
        window.__inputRecorder = null;
        return finalEvents;
    }
    window.__inputRecorder = null;
    return [];
}
"""

HIDE_PAGE_CURSOR_SCRIPT = """
() => {
    const id = "recorder-hide-cursor";
    document.getElementById(id)?.remove();
    const style = document.createElement("style");
    style.id = id;
    style.textContent = `
        *, *::before, *::after {
            cursor: none !important;
        }
    `;
    document.documentElement.appendChild(style);
}
"""

RESTORE_PAGE_CURSOR_SCRIPT = """
() => {
    document.getElementById("recorder-hide-cursor")?.remove();
}
"""

# Tiny overlay on the game tab that changes every tick so browser capture keeps
# emitting fresh frames on otherwise-static or inference-paused pages.
START_FORCE_CFR_DIRTY_LOOP_SCRIPT = """
(fps) => {
    const targetFps = Math.max(1, Number(fps) || 60);
    const existing = window.__forceCfr;
    if (existing && typeof existing.stop === "function") {
        existing.stop();
    }

    const canvas = document.createElement("canvas");
    canvas.id = "recorder-force-cfr";
    canvas.width = 1;
    canvas.height = 1;
    canvas.setAttribute("aria-hidden", "true");
    canvas.style.cssText = "position:fixed;left:0;top:0;width:1px;height:1px;opacity:1;pointer-events:none;z-index:2147483647;";
    document.documentElement.appendChild(canvas);

    const ctx = canvas.getContext("2d", {alpha: true});
    if (!ctx) {
        canvas.remove();
        throw new Error("Failed to create force-cfr dirty canvas.");
    }

    let frame = 0;
    let stopped = false;
    const paint = () => {
        if (stopped) return;
        const v = frame & 255;
        ctx.fillStyle = `rgb(${v}, ${(255 - v) & 255}, ${(frame * 5) & 255})`;
        ctx.fillRect(0, 0, 1, 1);
        frame += 1;
    };

    paint();
    // This heartbeat belongs to the recorder, not the game. In paused-inference
    // mode the game timer hooks are frozen, but capture must keep receiving
    // timestamped frames so a resume boundary does not duplicate a stale frame.
    const realSetInterval = window.__realSetInterval || window.setInterval.bind(window);
    const realClearInterval = window.__realClearInterval || window.clearInterval.bind(window);
    const timer = realSetInterval(paint, Math.max(1, Math.round(1000 / targetFps)));
    window.__forceCfr = {
        stop: () => {
            stopped = true;
            realClearInterval(timer);
            canvas.remove();
            delete window.__forceCfr;
        },
    };
    return {ok: true, fps: targetFps};
}
"""

STOP_FORCE_CFR_DIRTY_LOOP_SCRIPT = """
() => {
    const existing = window.__forceCfr;
    if (existing && typeof existing.stop === "function") {
        existing.stop();
        return {ok: true, stopped: true};
    }
    document.getElementById("recorder-force-cfr")?.remove();
    return {ok: true, stopped: false};
}
"""


@dataclass(slots=True)
class RecordingConfig:
    game_id: str
    task_id: str | None
    output_root: Path = Path("data/trajs/raw")
    port: int = 8101
    control_port: int = 8767
    suffix: str | None = None
    headless: bool = False
    video_backend: str = "extension"
    video_fps: int = 60
    video_bitrate: str = "4M"
    # Dirty one pixel on the game tab so browser capture receives fresh frames.
    force_cfr: bool = True
    state_fps: float = 5.0
    input_poll_s: float = 0.1
    duration_s: float | None = None
    post_terminal_s: float = 1.0
    record_raw_mousemove: bool = True
    mousemove_min_interval_ms: float = 0.0
    random_seed: int | None = 42
    stop_on_terminal: bool = True
    # None keeps whatever the task declares. Collection runs set it False so that a
    # failed attempt ends the episode instead of resetting and trying again, which
    # makes `duration_s` a ceiling rather than the thing that ends the recording.
    continue_on_fail: bool | None = None
    episode_id: str | None = None
    player_type: str = "human"
    max_steps: int | None = None
    # Real seconds the recording may run, model waits included. When they are spent
    # the episode ends like any other limit: a timeout with what was played so far.
    wall_clock_s: float | None = None
    actor_metadata: dict[str, Any] | None = None
    block_external_network: bool = True
    # The run plan this episode belongs to (as written), kept in the summary.
    run_plan: dict[str, Any] | None = None


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=_json_default)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            json.dump(record, handle, ensure_ascii=False, default=_json_default)
            handle.write("\n")


def _resolve_continue_on_fail(config: RecordingConfig, task: Any) -> bool:
    """Whether a failed attempt resets and plays on inside the same episode.

    The task's own value is the evaluation default: a Builder run wants several
    attempts within one time budget so a single unlucky death does not decide the
    verdict. Collection wants the opposite -- one attempt per episode, ending where
    the run ended -- because a trajectory containing a dozen resets is a dozen
    trajectories concatenated, and nothing downstream can tell where one stopped and
    the next began. The run therefore overrides the task rather than the catalog
    carrying two meanings of the same field.
    """
    if config.continue_on_fail is not None:
        return bool(config.continue_on_fail)
    return bool(getattr(task, "continue_on_fail", False))


def _safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value.strip())
    return cleaned.strip("_") or "episode"


def _is_browser_closed_error(exc: BaseException) -> bool:
    text = str(exc)
    return exc.__class__.__name__ == "TargetClosedError" or "has been closed" in text


def _validate_video_bitrate(value: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError("--video-bitrate cannot be empty.")
    suffix = cleaned[-1].lower()
    number = cleaned[:-1] if suffix in {"k", "m"} else cleaned
    if not number.isdigit() or int(number) <= 0:
        raise ValueError("--video-bitrate must be a positive ffmpeg bitrate such as 4000k or 4M.")
    return cleaned


def _video_bitrate_to_bps(value: str) -> int:
    cleaned = _validate_video_bitrate(value)
    suffix = cleaned[-1].lower()
    if suffix == "k":
        return int(cleaned[:-1]) * 1000
    if suffix == "m":
        return int(cleaned[:-1]) * 1_000_000
    return int(cleaned)


def _playwright_video_patch_source(*, fps: int, bitrate: str) -> str:
    return f"""
const fs = require('fs');
const Module = require('module');

const targetFps = {int(fps)};
const targetBitrate = {json.dumps(bitrate)};
const originalLoader = Module._extensions['.js'];

Module._extensions['.js'] = function(module, filename) {{
  if (!filename.endsWith('/server/chromium/videoRecorder.js')) {{
    return originalLoader(module, filename);
  }}

  let source = fs.readFileSync(filename, 'utf8');
  const original = source;
  source = source.replace(
    'async _launch(options) {{',
    'async _launch(options) {{ this._timingPath = options.outputFile + ".timing.json";'
  );
  source = source.replace(/const fps = \\d+;/, `const fps = ${{targetFps}};`);
  source = source.replace(
    '-c:v mjpeg -i - -y -an -r ${{fps}}',
    '-framerate ${{fps}} -c:v mjpeg -i - -y -an'
  );
  source = source.replace(/-b:v \\S+/, `-b:v ${{targetBitrate}}`);
  const repeatCountLine =
    'const repeatCount = Math.max(1, Math.round(fps * durationSec));';
  if (!source.includes(repeatCountLine)) {{
    throw new Error('Could not locate Playwright frame sampling logic.');
  }}
  source = source.replace(
    repeatCountLine,
    [
      'if (this._videoStartTimestamp === undefined) {{',
      '  this._videoStartTimestamp = this._lastFrameTimestamp;',
      '  this._emittedFrameCount = 0;',
      '  require("fs").writeFileSync(this._timingPath,',
      '    JSON.stringify({{first_frame_epoch_s: this._videoStartTimestamp}}));',
      '}}',
      'const targetFrameCount = Math.max(1, Math.round(',
      '  fps * (timestamp - this._videoStartTimestamp)',
      '));',
      'const repeatCount = Math.max(',
      '  0, targetFrameCount - this._emittedFrameCount',
      ');',
      'this._emittedFrameCount += repeatCount;'
    ].join('\\n')
  );

  if (source === original) {{
    throw new Error('Failed to patch Playwright video recorder.');
  }}
  return module._compile(source, filename);
}};
""".lstrip()


class _ExtensionIngestHandler(BaseHTTPRequestHandler):
    server: "_ExtensionIngestHTTPServer"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send_empty(self, status: HTTPStatus = HTTPStatus.NO_CONTENT) -> None:
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Recorder-Sequence")
        self.end_headers()

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_empty()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if parsed.path == "/video-chunk":
            query = parse_qs(parsed.query)
            sequence = int((query.get("seq") or ["0"])[0])
            self.server.ingest.write_video_chunk(sequence, body, self.headers.get("Content-Type"))
            self._send_json({"ok": True})
            return
        if parsed.path == "/extension-event":
            payload = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(payload, dict):
                raise ValueError("extension event payload must be an object")
            self._send_json({"ok": True})
            return
        self._send_json({"error": f"unknown endpoint: {parsed.path}"}, HTTPStatus.NOT_FOUND)


class _ExtensionIngestHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], ingest: "ExtensionIngestServer"):
        super().__init__(server_address, _ExtensionIngestHandler)
        self.ingest = ingest


class ExtensionIngestServer:
    """Receives tab-capture chunks from the recorder extension."""

    def __init__(self, *, episode_dir: Path):
        self.episode_dir = episode_dir
        self.video_path = episode_dir / "video.webm"
        self._server = _ExtensionIngestHTTPServer(("127.0.0.1", 0), self)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.chunk_count = 0
        self.byte_count = 0
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="extension-ingest",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=5)
        self._server.server_close()

    def write_video_chunk(self, _sequence: int, data: bytes, _content_type: str | None) -> None:
        with self._lock:
            with self.video_path.open("ab") as handle:
                handle.write(data)
            self.byte_count += len(data)
            self.chunk_count += 1

FREEPLAY_CONTROL_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trajectory Recorder</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #11161c;
      color: #edf2f7;
    }
    .panel {
      width: min(360px, calc(100vw - 24px));
      border: 1px solid #384656;
      border-radius: 8px;
      background: #1a222b;
      padding: 14px;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
    }
    h1 { margin: 0 0 12px; font-size: 16px; font-weight: 650; }
    .buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    button {
      border: 1px solid #435365;
      border-radius: 7px;
      background: #111820;
      color: #edf2f7;
      padding: 10px;
      font: inherit;
      cursor: pointer;
    }
    button:hover { border-color: #7db4ff; }
    button.primary { background: #17633c; border-color: #29965d; }
    button.danger { background: #5d2020; border-color: #954040; }
    pre {
      margin: 12px 0 0;
      min-height: 120px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: #b8c6d6;
      font-size: 12px;
      line-height: 1.45;
    }
  </style>
</head>
<body>
  <main class="panel">
    <h1>Trajectory Recorder</h1>
    <div class="buttons">
      <button id="start" class="primary">Start</button>
      <button id="stop">Stop</button>
      <button id="quit" class="danger">Quit</button>
    </div>
    <pre id="status">Loading...</pre>
  </main>
<script>
const statusNode = document.querySelector("#status");
async function request(path) {
  const response = await fetch(path, {method: "POST"});
  const payload = await response.json();
  statusNode.textContent = JSON.stringify(payload, null, 2);
}
async function refresh() {
  const response = await fetch("/status");
  const payload = await response.json();
  statusNode.textContent = JSON.stringify(payload, null, 2);
}
document.querySelector("#start").addEventListener("click", () => request("/start"));
document.querySelector("#stop").addEventListener("click", () => request("/stop"));
document.querySelector("#quit").addEventListener("click", () => request("/quit"));
setInterval(refresh, 1000);
refresh();
</script>
</body>
</html>
""".strip()


class _FreeplayControlHandler(BaseHTTPRequestHandler):
    server: "_FreeplayControlHTTPServer"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _run(self, coroutine: Any) -> dict[str, Any]:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.server.loop)
        return future.result()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"", "/"}:
            self._send_bytes(FREEPLAY_CONTROL_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/status":
            self._send_json(self.server.recorder.status())
            return
        self._send_json({"error": f"unknown endpoint: {parsed.path}"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/start":
                self._send_json(self._run(self.server.recorder.start_episode()))
            elif parsed.path == "/stop":
                self._send_json(self._run(self.server.recorder.stop_episode("manual", "control_panel")))
            elif parsed.path == "/quit":
                self._send_json(self._run(self.server.recorder.quit()))
            else:
                self._send_json({"error": f"unknown endpoint: {parsed.path}"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


class _FreeplayControlHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        recorder: "FreeplayTrajectoryRecorder",
        loop: asyncio.AbstractEventLoop,
    ):
        super().__init__(server_address, _FreeplayControlHandler)
        self.recorder = recorder
        self.loop = loop


class FreeplayControlServer:
    """Small local control page for starting and stopping freeplay episodes."""

    def __init__(
        self,
        *,
        recorder: "FreeplayTrajectoryRecorder",
        loop: asyncio.AbstractEventLoop,
        port: int,
        port_retries: int = 20,
    ):
        requested = int(port)
        last_error: OSError | None = None
        self._server: _FreeplayControlHTTPServer | None = None
        for candidate in range(requested, requested + max(1, int(port_retries))):
            try:
                self._server = _FreeplayControlHTTPServer(("127.0.0.1", candidate), recorder, loop)
                break
            except OSError as exc:
                last_error = exc
                if exc.errno not in {98, 48, 10048}:  # EADDRINUSE (Linux/macOS/Windows)
                    raise
        if self._server is None:
            raise OSError(
                f"Control panel port {requested} is busy "
                f"(tried {port_retries} ports). Last error: {last_error}"
            ) from last_error
        bound = int(self._server.server_address[1])
        if bound != requested:
            LOGGER.warning(
                "Control port %s busy; using %s instead. Pass --control-port %s to set explicitly.",
                requested,
                bound,
                bound,
            )
        self._thread: threading.Thread | None = None
        self.url = f"http://127.0.0.1:{bound}"
        self.port = bound

    def start(self) -> None:
        assert self._server is not None
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="freeplay-control",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        assert self._server is not None
        self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=5)
        self._server.server_close()


class TrajectoryRecorder:
    """Record a human-played browser game episode."""

    def __init__(self, config: RecordingConfig):
        self.config = config
        self.game_id = resolve_game_id(config.game_id)
        self.task_id = str(config.task_id).strip() if config.task_id is not None else None
        self.game = load_game(self.game_id)
        self.task = load_task(self.game_id, self.task_id) if self.task_id is not None else None
        self.continue_on_fail = (
            _resolve_continue_on_fail(config, self.task) if self.task is not None else False
        )
        self.evaluator = (
            build_task_evaluator(
                evaluator_id=self.task.evaluator_id,
                evaluator_config=self.task.evaluator_config,
                start_score=self.task.task_start_score_field,
                target_score=self.task.task_target_score_field,
                max_steps=None,
                continue_on_fail=self.continue_on_fail,
            )
            if self.task is not None
            else None
        )
        self.tracker = build_game_state_tracker()

        self._set_episode_paths(config.episode_id or self._build_episode_id())

        self.launcher: GameLauncher | None = None
        self.manager: BrowserGameManager | None = None
        self.game_url: str | None = None
        self._browser_profile: tempfile.TemporaryDirectory[str] | None = None
        self._playwright_video_dir: tempfile.TemporaryDirectory[str] | None = None
        self._playwright_video_path: Path | None = None
        self._playwright_timing_path: Path | None = None
        self._ingest_server: ExtensionIngestServer | None = None
        self._extension_controller_page: Any | None = None
        self._stop_event: asyncio.Event | None = None
        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None
        self._python_monotonic_start: float | None = None
        self._python_monotonic_end: float | None = None
        self._page_start_perf_ms: float | None = None
        self._page_time_origin_ms: float | None = None
        self._trajectory_start_epoch_ms: float | None = None
        self._video_started_monotonic: float | None = None
        self._video_time_offset_s: float | None = None
        self._video_time_offset_method: str | None = None
        self._video_timing_marker_events: list[dict[str, Any]] = []
        self._video_timing_error: str | None = None
        self._video_probe: dict[str, Any] | None = None
        self._clock_start: dict[str, Any] | None = None
        self._input_init_script_installed = False
        self._raw_input_index = 0
        self._state_sample_index = 0
        self._valid_state_samples = 0
        self._valid_policy_state_samples = 0
        self._last_progress: float | None = None
        self._last_task_status: str | None = None
        self._actor_steps = 0
        self._reset_count = 0
        self._reset_in_progress = False
        self._eval_metrics: dict[str, Any] = {}
        self._stop_deadline: float | None = None
        self._stop_reason: str | None = None
        self._outcome = "unknown"
        self._error: str | None = None
        # Set when the agent reports the game as broken, or when the page's renderer dies.
        self._report: dict[str, Any] | None = None
        self._page_crash_t: float | None = None
        self._page_crashed: asyncio.Event | None = None
        self._browser_version_value: str | None = None
        self._blocked_external_requests = 0
        self._loop_signal_handlers: list[signal.Signals] = []
        self._previous_signal_handlers: dict[signal.Signals, Any] = {}
        self._gameplay_ready: asyncio.Event | None = None
        self._recording_io_lock = asyncio.Lock()
        self._inference_pauses: list[dict[str, float]] = []
        self._active_inference_pause: dict[str, float] | None = None

    def _set_episode_paths(self, episode_id: str) -> None:
        self.episode_id = episode_id
        self.episode_dir = (self.config.output_root / self.game_id / self.episode_id).resolve()
        self.video_path = self.episode_dir / "video.webm"
        self.summary_path = self.episode_dir / "summary.json"
        self.inputs_path = self.episode_dir / "inputs.jsonl"
        self.states_path = self.episode_dir / "states.jsonl"

    @property
    def outcome(self) -> str:
        return self._outcome

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    @property
    def final_metrics(self) -> dict[str, Any]:
        return dict(self._eval_metrics)

    @property
    def actor_steps(self) -> int:
        return self._actor_steps

    def _build_episode_id(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return _slug(f"{self.game_id}_{self.task_id or 'freeplay'}_{stamp}")

    def _validate_config(self) -> None:
        if self.config.video_backend not in {"extension", "playwright"}:
            raise ValueError("--video-backend must be extension or playwright.")
        if self.config.video_backend == "extension" and self.config.headless:
            raise ValueError(
                "--video-backend extension requires headed Chromium. "
                "Run without --headless, or use --video-backend playwright for Chrome headless."
            )
        if self.config.video_fps <= 0:
            raise ValueError("--video-fps must be greater than 0.")
        _validate_video_bitrate(self.config.video_bitrate)
        if self.config.state_fps < 0:
            raise ValueError("--state-fps cannot be negative.")
        if self.config.input_poll_s <= 0:
            raise ValueError("--input-poll-ms must be greater than 0.")
        if self.config.duration_s is not None and self.config.duration_s <= 0:
            raise ValueError("--duration-s must be greater than 0 when provided.")
        if self.config.wall_clock_s is not None and self.config.wall_clock_s <= 0:
            raise ValueError("--wall-clock-s must be greater than 0 when provided.")
        if self.config.post_terminal_s < 0:
            raise ValueError("--post-terminal-s cannot be negative.")
        if self.config.mousemove_min_interval_ms < 0:
            raise ValueError("--mousemove-min-interval-ms cannot be negative.")
        if self.config.max_steps is not None and self.config.max_steps <= 0:
            raise ValueError("--max-steps must be greater than 0 when provided.")

    def _prepare_output_dir(self) -> None:
        if self.episode_dir.exists():
            raise FileExistsError(f"Episode directory already exists: {self.episode_dir}")
        self.episode_dir.mkdir(parents=True)
        for path in (
            self.video_path,
            self.inputs_path,
            self.states_path,
        ):
            path.touch()

    @contextmanager
    def _playwright_video_patch_env(self):
        fd, path = tempfile.mkstemp(prefix="pw_video_", suffix=".js", dir=temporary_dir("browser"))
        os.close(fd)
        patch_path = Path(path)
        patch_path.write_text(
            _playwright_video_patch_source(
                fps=self.config.video_fps,
                bitrate=_validate_video_bitrate(self.config.video_bitrate),
            ),
            encoding="utf-8",
        )
        old_node_options = os.environ.get("NODE_OPTIONS")
        require_option = f"--require {json.dumps(str(patch_path))}"
        os.environ["NODE_OPTIONS"] = (
            f"{old_node_options} {require_option}" if old_node_options else require_option
        )
        try:
            yield
        finally:
            if old_node_options is None:
                os.environ.pop("NODE_OPTIONS", None)
            else:
                os.environ["NODE_OPTIONS"] = old_node_options
            patch_path.unlink(missing_ok=True)

    def _elapsed_t(self) -> float:
        if self._python_monotonic_start is None:
            return 0.0
        end = self._python_monotonic_end or time.monotonic()
        return max(
            0.0,
            end - self._python_monotonic_start - self._paused_duration_s(end),
        )

    def _wall_clock_spent(self) -> bool:
        return (
            self.config.wall_clock_s is not None
            and self._python_monotonic_start is not None
            and time.monotonic() - self._python_monotonic_start >= self.config.wall_clock_s
        )

    def _paused_duration_s(self, end_monotonic: float | None = None) -> float:
        duration = sum(
            pause["end_monotonic"] - pause["start_monotonic"]
            for pause in self._inference_pauses
        )
        if self._active_inference_pause is not None:
            end = end_monotonic or time.monotonic()
            duration += max(0.0, end - self._active_inference_pause["start_monotonic"])
        return max(0.0, duration)

    def _paused_epoch_ms_before(self, epoch_ms: float) -> float:
        paused_ms = 0.0
        intervals = list(self._inference_pauses)
        if self._active_inference_pause is not None:
            intervals.append({**self._active_inference_pause, "end_epoch_ms": epoch_ms})
        for pause in intervals:
            start = pause["start_epoch_ms"]
            end = pause["end_epoch_ms"]
            paused_ms += max(0.0, min(epoch_ms, end) - start)
        return paused_ms

    async def _begin_inference_pause(self) -> bool:
        """Freeze gameplay and stop recorder sampling until inference completes."""
        if self.manager is None or self._gameplay_ready is None or not self._page_alive():
            return False
        if self._active_inference_pause is not None:
            return True

        self._gameplay_ready.clear()
        async with self._recording_io_lock:
            # A reload may have finished while this pause waited for the lock.
            self._gameplay_ready.clear()
            try:
                await self._drain_input_events()
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Input drain before inference pause failed: %s", exc)
            result = await self.manager.pause_game()
            if not result or result.get("ok") is not True:
                self._gameplay_ready.set()
                return False
            epoch_ms = _safe_float(result.get("realEpochMs"))
            if epoch_ms is None:
                try:
                    epoch_ms = _safe_float((await self._page_clock()).get("epoch_ms"))
                except Exception as exc:  # noqa: BLE001
                    LOGGER.debug("Clock read during inference pause failed: %s", exc)
            if epoch_ms is None:
                self._gameplay_ready.set()
                await self.manager.resume_game()
                return False
            self._active_inference_pause = {
                "start_monotonic": time.monotonic(),
                "start_epoch_ms": epoch_ms,
            }
        return True

    async def _end_inference_pause(self) -> None:
        """Resume gameplay and close the current removable video interval."""
        pause = self._active_inference_pause
        if pause is None or not self._page_alive():
            if self._gameplay_ready is not None:
                self._gameplay_ready.set()
            return

        try:
            async with self._recording_io_lock:
                try:
                    await self._clear_input_buffers()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.debug("Input clear after inference pause failed: %s", exc)
                result = await self.manager.resume_game() if self.manager else None
                epoch_ms = _safe_float((result or {}).get("realEpochMs"))
                if epoch_ms is None and self.manager is not None:
                    try:
                        epoch_ms = _safe_float((await self._page_clock()).get("epoch_ms"))
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.debug("Clock read after inference pause failed: %s", exc)
                end_monotonic = time.monotonic()
                end_epoch_ms = epoch_ms or (
                    pause["start_epoch_ms"]
                    + (end_monotonic - pause["start_monotonic"]) * 1000.0
                )
                self._inference_pauses.append(
                    {
                        **pause,
                        "end_monotonic": end_monotonic,
                        "end_epoch_ms": max(pause["start_epoch_ms"], end_epoch_ms),
                    }
                )
                self._active_inference_pause = None
        finally:
            if self._gameplay_ready is not None:
                self._gameplay_ready.set()

    def _request_stop(self, outcome: str, reason: str) -> None:
        if self._stop_event and not self._stop_event.is_set():
            self._outcome = outcome
            self._stop_reason = reason
            self._stop_event.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig,
                    self._request_stop,
                    "interrupted",
                    sig.name,
                )
                self._loop_signal_handlers.append(sig)
            except (NotImplementedError, RuntimeError):
                self._previous_signal_handlers[sig] = signal.getsignal(sig)

                def request_stop(signum, _frame, *, stop_signal=sig) -> None:
                    loop.call_soon_threadsafe(
                        self._request_stop,
                        "interrupted",
                        signal.Signals(signum).name if signum else stop_signal.name,
                    )

                signal.signal(sig, request_stop)

    def _restore_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in self._loop_signal_handlers:
            loop.remove_signal_handler(sig)
        self._loop_signal_handlers.clear()

        for sig, previous in self._previous_signal_handlers.items():
            signal.signal(sig, previous)
        self._previous_signal_handlers.clear()

    def _mark_interrupted(self, reason: str) -> None:
        self._outcome = "interrupted"
        self._stop_reason = self._stop_reason or reason

    def _is_interrupted(self) -> bool:
        """Whether the episode ended for an outside reason (a signal, a closed browser or
        a crashed page), after which failing cleanup steps are expected, not new errors."""
        return self._outcome in {"interrupted", "page_crashed"}

    def _on_page_crash(self, _page: Any = None) -> None:
        """The renderer process died (a game bug, or the host running out of memory)."""
        self._page_crash_t = self._elapsed_t()
        self._outcome = "page_crashed"
        self._stop_reason = "page_crash"
        self._error = f"page crashed: the renderer process died at t={self._page_crash_t:.2f}s"
        if self._stop_event is not None:
            self._stop_event.set()
        if self._page_crashed is not None:
            self._page_crashed.set()

    def _page_alive(self) -> bool:
        """False once the renderer died: any further call into the page never returns."""
        return self._page_crash_t is None

    def _git_metadata(self) -> dict[str, Any]:
        repo_root = Path(__file__).resolve().parents[2]

        def run_git(args: list[str]) -> str | None:
            try:
                result = subprocess.run(
                    ["git", *args],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except FileNotFoundError:
                return None
            if result.returncode != 0:
                return None
            return result.stdout.strip()

        status = run_git(["status", "--short"])
        return {
            "commit": run_git(["rev-parse", "HEAD"]),
            "dirty": bool(status),
        }

    def _roles_metadata(self) -> list[dict[str, Any]]:
        roles = []
        for role in self.game.game_roles:
            roles.append(
                {
                    "name": role.name,
                    "controls": {
                        "allowed_keys": sorted(role.controls.allowed_keys),
                        "hold_duration": role.controls.hold_duration,
                        "allow_clicks": role.controls.allow_clicks,
                    },
                    "context": {
                        "role_description": role.context.role_description,
                        "control_notes": role.context.control_notes,
                    },
                }
            )
        return roles

    def _browser_version(self) -> str | None:
        if self._browser_version_value is not None:
            return self._browser_version_value
        browser = self.manager.browser if self.manager else None
        if browser is None:
            return None
        version = getattr(browser, "version", None)
        return version() if callable(version) else version

    def _summary(self) -> dict[str, Any]:
        final_metrics = dict(self._eval_metrics or {})
        final_score = _first_present(final_metrics, ("score", "score_best", "score_current"))
        final_progress = _first_present(
            final_metrics,
            ("progress", "progress_best", "progress_current"),
        )
        task_completed: bool | None = None
        if self.task is not None:
            if self._outcome == "success" or final_metrics.get("target_reached") is True:
                task_completed = True
            elif self._valid_state_samples > 0 and self._last_task_status != "error":
                task_completed = False
        state_coverage = (
            self._valid_state_samples / self._state_sample_index
            if self._state_sample_index
            else 0.0
        )
        return {
            "schema_version": 2,
            "episode_id": self.episode_id,
            "game_id": self.game_id,
            "mode": "task" if self.task is not None else "freeplay",
            "task": (
                {
                    "id": self.task_id,
                    "instruction": self.task.task_prompt,
                    "evaluator": self.task.evaluator_id,
                    "evaluator_config": self.task.evaluator_config,
                    "start_score": self.task.task_start_score_field,
                    "target_score": self.task.task_target_score_field,
                    "max_steps": self.task.max_steps,
                    "continue_on_fail": self.continue_on_fail,
                }
                if self.task is not None
                else None
            ),
            "game_url": self.game_url,
            "actor": {
                "type": self.config.player_type,
                **dict(self.config.actor_metadata or {}),
                "steps": self._actor_steps,
            },
            "interface": "keyboard_mouse",
            "browser": {
                "engine": "chromium",
                "version": self._browser_version(),
                "headless": self.config.headless,
                "video_backend": self.config.video_backend,
                "offline": self.config.block_external_network,
                "blocked_external_requests": max(
                    self._blocked_external_requests,
                    len(self.manager.blocked_external_urls) if self.manager else 0,
                ),
            },
            "screen_size": [self.game.width, self.game.height],
            "timing": {
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "ended_at": self._ended_at.isoformat() if self._ended_at else None,
                "duration_s": round(self._elapsed_t(), 6),
                "pause_count": len(self._inference_pauses),
                "paused_duration_s": round(self._paused_duration_s(), 6),
                "video_time_offset_s": self._video_time_offset_s,
                "video_time_offset_method": self._video_time_offset_method,
                **(
                    {"video_time_offset_error": self._video_timing_error}
                    if self._video_timing_error
                    else {}
                ),
                "page_clock_start": self._clock_start,
            },
            "random_seed": self.config.random_seed,
            "repository": self._git_metadata(),
            "catalog": {
                "game_rules": self.game.game_rules,
                "speed_multiplier": self.game.speed_multiplier,
                "roles": self._roles_metadata(),
            },
            "video": {
                "path": "video.webm",
                "fps": self.config.video_fps,
                "bitrate": self.config.video_bitrate,
                "force_cfr": bool(self.config.force_cfr),
                "codec": ((self._video_probe or {}).get("streams") or [{}])[0].get("codec_name", "vp8"),
                "container": "webm",
                "size": [self.game.width, self.game.height],
                "probe": self._video_probe,
                "chunks": self._ingest_server.chunk_count if self._ingest_server else None,
                "bytes": self._ingest_server.byte_count if self._ingest_server else None,
            },
            "inputs": {
                "path": "inputs.jsonl",
                "count": self._raw_input_index,
                "poll_s": self.config.input_poll_s,
                "raw_mousemove": self.config.record_raw_mousemove,
                "mousemove_min_interval_ms": self.config.mousemove_min_interval_ms,
            },
            "states": {
                "path": "states.jsonl",
                "fps": self.config.state_fps,
                "count": self._state_sample_index,
                "valid_count": self._valid_state_samples,
                "coverage": round(state_coverage, 6),
                "policy_valid_count": self._valid_policy_state_samples,
                "policy_coverage": round(
                    self._valid_policy_state_samples / self._state_sample_index,
                    6,
                )
                if self._state_sample_index
                else 0.0,
            },
            "result": {
                "outcome": self._outcome,
                "stop_reason": self._stop_reason,
                "task_completed": task_completed,
                "score": final_score,
                "progress": final_progress,
                "metrics": final_metrics,
                "resets": self._reset_count,
            },
            "report": self._report,
            "plan": self.config.run_plan,
            "error": self._error,
        }

    def _write_summary(self) -> None:
        _write_json(self.summary_path, self._summary())

    async def _page_clock(self) -> dict[str, Any]:
        page = self.manager.page if self.manager else None
        if page is None:
            raise RuntimeError("Browser page is unavailable.")
        recovered = False
        try:
            clock = await page.evaluate(PAGE_CLOCK_SCRIPT)
        except PlaywrightError as exc:
            if not await self._recover_navigation(exc):
                raise
            recovered = True
            clock = await page.evaluate(PAGE_CLOCK_SCRIPT)
        if not isinstance(clock, dict):
            raise RuntimeError("Browser page returned an invalid clock payload.")
        origin = clock.get("time_origin_ms")
        if self._page_time_origin_ms is not None and origin != self._page_time_origin_ms and not recovered:
            await self._recover_navigation()
            clock = await page.evaluate(PAGE_CLOCK_SCRIPT)
        self._page_time_origin_ms = clock.get("time_origin_ms")
        return clock

    async def _recover_navigation(self, exc: PlaywrightError | None = None) -> bool:
        """Wait out a page reload without turning it into a failed recording."""
        if self.manager is None or (exc is not None and "Execution context was destroyed" not in str(exc)):
            return False
        ready = self._gameplay_ready
        was_ready = ready is not None and ready.is_set()
        if ready is not None:
            ready.clear()
        LOGGER.info("Page reloaded; waiting for the game to become actionable")
        if not await self.manager.wait_until_actionable(
            stage="reload",
            timeout_s=self.manager.config.readiness_timeout_s or RESET_TIMEOUT_S,
        ):
            raise GameInterfaceError("reset", "Reloaded game did not become actionable") from exc
        if was_ready:
            ready.set()
        return True

    def _page_t_from_perf_ms(self, perf_ms: float | None) -> float:
        if perf_ms is None or self._page_start_perf_ms is None:
            return self._elapsed_t()
        return max(0.0, (perf_ms - self._page_start_perf_ms) / 1000.0)

    def _trajectory_t(self, *, epoch_ms: float | None, perf_ms: float | None) -> float:
        """Map page timestamps onto the episode clock, including across reloads."""
        if epoch_ms is not None and self._trajectory_start_epoch_ms is not None:
            return max(
                0.0,
                (
                    epoch_ms
                    - self._trajectory_start_epoch_ms
                    - self._paused_epoch_ms_before(epoch_ms)
                )
                / 1000.0,
            )
        return self._page_t_from_perf_ms(perf_ms)

    async def _install_input_recorder(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            raise RuntimeError("Browser page is unavailable.")
        options = {
            "recordRawMousemove": self.config.record_raw_mousemove,
            "mousemoveMinIntervalMs": self.config.mousemove_min_interval_ms,
        }
        if not self._input_init_script_installed:
            init_script = f"({INSTALL_INPUT_RECORDER_SCRIPT})({json.dumps(options)})"
            await page.add_init_script(script=init_script)
            self._input_init_script_installed = True
        await page.evaluate(INSTALL_INPUT_RECORDER_SCRIPT, options)
        await self._clear_input_buffers()

    async def _clear_input_buffers(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            raise RuntimeError("Browser page is unavailable.")
        await page.evaluate(DRAIN_INPUT_RECORDER_SCRIPT)

    async def _reset_input_state(self, reason: str) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            raise RuntimeError("Browser page is unavailable.")
        await page.evaluate(RESET_INPUT_RECORDER_SCRIPT, reason)

    async def _dispose_input_recorder(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None or page.is_closed():
            return
        raw_events = await page.evaluate(DISPOSE_INPUT_RECORDER_SCRIPT)
        await self._write_input_events(raw_events)

    async def _hide_page_cursor(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            raise RuntimeError("Browser page is unavailable.")
        await page.evaluate(HIDE_PAGE_CURSOR_SCRIPT)

    async def _restore_page_cursor(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None or page.is_closed():
            return
        await page.evaluate(RESTORE_PAGE_CURSOR_SCRIPT)

    async def _start_force_cfr_dirty_loop(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            raise RuntimeError("Browser page is unavailable.")
        await page.evaluate(START_FORCE_CFR_DIRTY_LOOP_SCRIPT, int(self.config.video_fps))

    async def _stop_force_cfr_dirty_loop(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None or page.is_closed():
            return
        await page.evaluate(STOP_FORCE_CFR_DIRTY_LOOP_SCRIPT)

    async def _drain_input_events(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            raise RuntimeError("Browser page is unavailable.")
        raw_events = await page.evaluate(DRAIN_INPUT_RECORDER_SCRIPT)
        await self._write_input_events(raw_events)

    async def _write_input_events(self, raw_events: Any) -> None:
        if not isinstance(raw_events, list):
            raise RuntimeError("Input recorder returned a non-list payload.")

        records: list[dict[str, Any]] = []
        for event in raw_events:
            if not isinstance(event, dict):
                continue
            t = self._trajectory_t(
                epoch_ms=_safe_float(event.get("t_epoch_ms")),
                perf_ms=_safe_float(event.get("t_perf_ms")),
            )
            record = {
                key: value
                for key, value in event.items()
                if key not in {"sequence", "t_perf_ms", "t_epoch_ms"} and value is not None
            }
            record["index"] = self._raw_input_index
            record["t"] = round(t, 6)
            records.append(record)
            self._raw_input_index += 1
        _append_jsonl(self.inputs_path, records)

    def _write_actor_decision(
        self,
        *,
        step_index: int,
        actions: list[dict[str, Any]],
        thinking: str | None,
        note: str | None = None,
        model_output: str | None = None,
        observation_t: float | None = None,
        valid: list[bool] | None = None,
        finish_reason: str | None = None,
        report: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Place an automated actor's decision on the normal input timeline.

        `t` is when the decision is executed; `observation_t` when the screen it was
        made from was captured; `valid[i]` whether the executor accepts action i (an
        invalid action is skipped, not executed).
        """
        record: dict[str, Any] = {
            "type": "agent_decision",
            "index": self._raw_input_index,
            "t": round(self._elapsed_t(), 6),
            "step": step_index,
            "actions": actions,
        }
        if observation_t is not None:
            record["observation_t"] = round(observation_t, 6)
        if valid is not None:
            record["valid"] = valid
        if finish_reason is not None:
            record["finish_reason"] = finish_reason
        if report is not None:
            record["report"] = report
        if usage:
            record["usage"] = usage
        if thinking:
            record["thinking"] = thinking
        if note:
            record["note"] = note
        if model_output:
            record["model_output"] = model_output
        self._raw_input_index += 1
        _append_jsonl(self.inputs_path, [record])

    def _write_executed_input(self, event: dict[str, Any]) -> None:
        """Place one low-level input an automated actor injected on the input timeline.

        `event` names the page event it causes (mousedown, keyup, wheel, ...) with the
        page's field names, and the action it belongs to. Unlike the events the page
        reports, these are exactly what was injected: a page cannot hide them, and
        events the page synthesizes itself are not among them.
        """
        record = {"type": "executed_input", **{k: v for k, v in event.items() if v is not None},
                  "index": self._raw_input_index, "t": round(self._elapsed_t(), 6)}
        self._raw_input_index += 1
        _append_jsonl(self.inputs_path, [record])

    async def _sample_state(self, *, finalized: bool = False) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            raise RuntimeError("Browser page is unavailable.")

        before = await self._page_clock()
        state, policy_state = await self._capture_state_bundle(page)
        after = await self._page_clock()
        if before.get("time_origin_ms") != after.get("time_origin_ms"):
            # A state and timestamps from different documents are not one sample.
            return
        before_epoch_ms = _safe_float(before.get("epoch_ms"))
        after_epoch_ms = _safe_float(after.get("epoch_ms"))
        before_perf_ms = _safe_float(before.get("perf_ms"))
        after_perf_ms = _safe_float(after.get("perf_ms"))
        sample_t = self._trajectory_t(
            epoch_ms=(before_epoch_ms + after_epoch_ms) / 2.0
            if before_epoch_ms is not None and after_epoch_ms is not None
            else None,
            perf_ms=(before_perf_ms + after_perf_ms) / 2.0
            if before_perf_ms is not None and after_perf_ms is not None
            else None,
        )

        sample_id = self._state_sample_index
        if state is not None:
            self._valid_state_samples += 1
        if policy_state is not None:
            self._valid_policy_state_samples += 1

        result = None
        if self.evaluator is not None:
            result = await self.evaluator(
                state=state,
                step_index=sample_id,
                metrics=self._eval_metrics,
                finalized=finalized,
            )
            if result.metrics:
                self._eval_metrics = dict(result.metrics)

        task_state: dict[str, Any] | None = None
        if result is not None:
            self._last_task_status = result.status
            metrics = dict(result.metrics or {})
            progress = _safe_float(
                _first_present(metrics, ("progress", "progress_best", "progress_current"))
            )
            progress_delta = (
                progress - self._last_progress
                if progress is not None and self._last_progress is not None
                else None
            )
            if progress is not None:
                self._last_progress = progress
            task_state = {
                "status": result.status,
                "completed": (
                    True
                    if result.status == "success"
                    else False if result.status == "fail" else None
                ),
                "summary": result.summary,
                "score": _first_present(metrics, ("score_current", "score", "score_best")),
                "score_best": metrics.get("score_best"),
                "progress": progress,
                "progress_delta": progress_delta,
                "should_stop": result.should_stop,
                "should_reset": result.should_reset,
                "stop_reason": result.stop_reason,
                "metrics": metrics,
            }
        _append_jsonl(
            self.states_path,
            [
                {
                    "sample_id": sample_id,
                    "t": round(sample_t, 6),
                    "finalized": finalized,
                    "state": state,
                    "policy_state": policy_state,
                    "task": task_state,
                }
            ],
        )
        self._state_sample_index += 1
        if result is not None and not finalized:
            if result.should_reset:
                await self._reset_failed_task()
                return
        if (
            result is not None
            and result.should_stop
            and self.config.stop_on_terminal
            and self._stop_deadline is None
            and (self._stop_event is None or not self._stop_event.is_set())
        ):
            self._outcome = result.status if result.status in {"success", "fail"} else "terminal"
            self._stop_reason = result.stop_reason or result.summary or result.status
            self._stop_deadline = self._elapsed_t() + max(0.0, self.config.post_terminal_s)

    async def _reset_failed_task(self) -> None:
        """Reset the attempt; a broken reset is reported for environment repair."""
        if self.manager is None:
            self._request_stop("fail", "reset_failed")
            return
        if self._gameplay_ready is not None:
            self._gameplay_ready.clear()
        try:
            try:
                await self._drain_input_events()
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Input drain before task reset failed: %s", exc)
            self._reset_in_progress = True
            await self.manager.reset_game(wait_actionable=True)
            await self._begin_attempt("task_reset")
            if self._gameplay_ready is not None:
                self._gameplay_ready.set()
        finally:
            self._reset_in_progress = False

    async def _begin_attempt(self, reason: str) -> None:
        """Start the next attempt: fresh attempt-local metrics and a fresh actor."""
        try:
            await self._reset_input_state(reason)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Input reset marker failed after task reset: %s", exc)
            self._request_stop("fail", "reset_failed")
            return
        self._eval_metrics = reset_task_evaluator_episode_metrics(
            self._eval_metrics, self.task.evaluator_id if self.task else None,
        )
        self._reset_count += 1
        await self._after_task_reset()

    async def _after_task_reset(self) -> None:
        """Allow an automated actor to clear attempt-local state."""
        return None

    async def _capture_state_bundle(
        self, page: Any
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Capture trusted state plus optional policy-only state."""
        captured = await self.tracker.capture(page)
        return (captured if isinstance(captured, dict) else None, None)

    def _page_init_scripts(self) -> tuple[str, ...]:
        return ()

    async def _input_loop(self) -> None:
        while self._stop_event and not self._stop_event.is_set():
            if self._gameplay_ready is not None:
                await self._gameplay_ready.wait()
            try:
                async with self._recording_io_lock:
                    if self._gameplay_ready is None or self._gameplay_ready.is_set():
                        await self._drain_input_events()
            except Exception as exc:
                if not self._reset_in_progress:
                    if not isinstance(exc, PlaywrightError) or not await self._recover_navigation(exc):
                        raise
                else:
                    await self._gameplay_ready.wait()
            await asyncio.sleep(max(0.01, self.config.input_poll_s))

    async def _state_loop(self) -> None:
        interval = 1.0 / self.config.state_fps
        while self._stop_event and not self._stop_event.is_set():
            if self._gameplay_ready is not None:
                await self._gameplay_ready.wait()
            started = asyncio.get_running_loop().time()
            try:
                async with self._recording_io_lock:
                    if self._gameplay_ready is None or self._gameplay_ready.is_set():
                        await self._sample_state()
            except PlaywrightError as exc:
                if not await self._recover_navigation(exc):
                    raise
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def _stop_at_limit(self, reason: str) -> None:
        # The last action may finish the task before the state loop polls it.
        if self.evaluator is not None:
            async with self._recording_io_lock:
                await self._sample_state(finalized=True)
        if self._stop_deadline is not None:
            self._request_stop(self._outcome, self._stop_reason or "terminal")
        else:
            self._request_stop("timeout", reason)

    async def _wait_for_stop(self) -> None:
        while self._stop_event and not self._stop_event.is_set():
            if self._stop_deadline is not None:
                if self._elapsed_t() >= self._stop_deadline:
                    self._request_stop(self._outcome, self._stop_reason or "terminal")
                    break
            elif self.config.duration_s is not None and self._elapsed_t() >= self.config.duration_s:
                await self._stop_at_limit("duration")
                break
            elif self._wall_clock_spent():
                await self._stop_at_limit("wall_clock")
                break
            await asyncio.sleep(0.05)

    def _extension_dir(self) -> Path:
        return Path(__file__).with_name("browser_extension")

    async def _start_browser(
        self,
        *,
        wait_actionable: bool = True,
        show_recording_gate: bool = True,
    ) -> None:
        suffix = self.config.suffix
        if suffix is None and self.task is not None:
            suffix = self.task.game_url_suffix
        self.launcher = GameLauncher(game_name=self.game.game_name, port=self.config.port)
        self.game_url = append_url_suffix(self.launcher.start(), suffix)
        record_video_dir = None
        if self.config.video_backend == "playwright":
            self._playwright_video_dir = tempfile.TemporaryDirectory(prefix="recorder_video_", dir=temporary_dir("browser"))
            record_video_dir = Path(self._playwright_video_dir.name)
        extension_dir = None
        user_data_dir = None
        if self.config.video_backend == "extension":
            self._browser_profile = tempfile.TemporaryDirectory(prefix="recorder_extension_profile_", dir=temporary_dir("browser"))
            extension_dir = self._extension_dir()
            user_data_dir = Path(self._browser_profile.name)
        try:
            unity_profile = load_profile_for_game(self.game_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "Unity WebGL state profile is invalid; gameplay will continue without it: %s",
                exc,
            )
            unity_profile = None
        self.manager = BrowserGameManager(
            BrowserConfig(
                game_url=self.game_url,
                width=self.game.width,
                height=self.game.height,
                headless=self.config.headless,
                speed_multiplier=self.game.speed_multiplier,
                readiness_timeout_s=self.game.readiness_timeout_s,
                random_seed=self.config.random_seed,
                game_api_init_options=getattr(self.task, "game_api_init_options", {}),
                record_video_dir=record_video_dir,
                record_video_size=(self.game.width, self.game.height),
                extension_dir=extension_dir,
                user_data_dir=user_data_dir,
                unity_profile=unity_profile,
                block_external_network=self.config.block_external_network,
                show_recording_gate=show_recording_gate,
                page_init_scripts=self._page_init_scripts(),
            )
        )
        if self.config.video_backend == "playwright":
            with self._playwright_video_patch_env():
                await self.manager.start(wait_actionable=wait_actionable)
            # Popups get their own recordings too. Remember the game page's
            # exact video before close() clears the page and Playwright handles.
            self._playwright_video_path = Path(await self.manager.page.video.path())
            self._playwright_timing_path = Path(str(self._playwright_video_path) + ".timing.json")
            self._video_started_monotonic = self.manager.video_started_monotonic
            if self.config.force_cfr:
                await self._start_force_cfr_dirty_loop()
        else:
            await self.manager.start(wait_actionable=wait_actionable)
        if self.manager.page is None:
            raise RuntimeError("Browser page is unavailable after startup.")
        self._browser_version_value = self._browser_version()
        await self.manager.page.evaluate(FOCUS_PAGE_SCRIPT)

    async def _extension_id(self) -> str:
        context = self.manager.context if self.manager else None
        if context is None:
            raise RuntimeError("Browser context is unavailable.")
        workers = context.service_workers
        worker = workers[0] if workers else await context.wait_for_event("serviceworker", timeout=5000)
        url = worker.url
        if not url.startswith("chrome-extension://"):
            raise RuntimeError(f"Unexpected extension service worker URL: {url}")
        return url.split("/")[2]

    async def _extension_status(self, controller: Any) -> dict[str, Any]:
        text = await controller.locator("#status").text_content()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"state": text.strip()}
        return payload if isinstance(payload, dict) else {}

    async def _wait_extension_status(
        self,
        controller: Any,
        expected_states: set[str],
        *,
        timeout_s: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status = await self._extension_status(controller)
            if status.get("state") in expected_states:
                return status
            await asyncio.sleep(0.05)
        raise TimeoutError(f"Timed out waiting for extension status {sorted(expected_states)}: {status}")

    async def _start_extension_video(self) -> None:
        if self._ingest_server is None:
            raise RuntimeError("Extension ingest server is not running.")
        if self.manager is None or self.manager.context is None or self.manager.page is None:
            raise RuntimeError("Browser is unavailable.")
        if self.game_url is None:
            raise RuntimeError("Game URL is unavailable.")

        extension_id = await self._extension_id()
        await self.manager.page.bring_to_front()
        await self._hide_page_cursor()
        try:
            if self.config.force_cfr:
                await self._start_force_cfr_dirty_loop()
            query = urlencode(
                {
                    "ingestUrl": self._ingest_server.url,
                    "targetUrl": self.game_url,
                    "fps": str(int(self.config.video_fps)),
                    "bitrate": str(_video_bitrate_to_bps(self.config.video_bitrate)),
                    "chunkMs": "1000",
                    "width": str(int(self.game.width)),
                    "height": str(int(self.game.height)),
                }
            )
            controller = await self.manager.context.new_page()
            self._extension_controller_page = controller
            await controller.goto(f"chrome-extension://{extension_id}/controller.html?{query}")
            await controller.bring_to_front()
            await controller.evaluate("() => window.__startRecording()")
            status = await self._wait_extension_status(
                controller,
                {"recording", "error"},
                timeout_s=15.0,
            )
            if not isinstance(status, dict) or status.get("state") != "recording":
                raise RuntimeError(f"Extension video recorder did not start: {status}")
            self._video_started_monotonic = time.monotonic()
        except Exception:
            await self._stop_force_cfr_dirty_loop()
            await self._restore_page_cursor()
            raise

    async def _show_video_sync_marker(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            return
        try:
            self._video_timing_marker_events = await show_sync_marker(page)
        except Exception as exc:  # noqa: BLE001
            self._video_timing_marker_events = []
            self._video_timing_error = f"marker_display_failed: {exc}"
            LOGGER.warning(
                "Video synchronization marker failed; using timing fallback: %s",
                exc,
            )

    def _set_video_time_offset_estimate(self) -> None:
        if self._python_monotonic_start is None:
            return
        if self._video_started_monotonic is None:
            self._video_time_offset_s = 0.0
            self._video_time_offset_method = "zero_fallback"
        else:
            self._video_time_offset_s = round(
                self._video_started_monotonic - self._python_monotonic_start,
                6,
            )
            self._video_time_offset_method = "start_time_estimate"

    def _calibrate_video_time_offset(self) -> None:
        capture_error = None
        if self.config.video_backend == "playwright":
            try:
                if self._playwright_timing_path is None or self._trajectory_start_epoch_ms is None:
                    raise ValueError("Capture timestamp or trajectory start is unavailable")
                timing = json.loads(self._playwright_timing_path.read_text())
                first_frame = float(timing["first_frame_epoch_s"])
                if not math.isfinite(first_frame):
                    raise ValueError("Capture timestamp is not finite")
                self._video_time_offset_s = round(
                    first_frame - self._trajectory_start_epoch_ms / 1000.0, 6,
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                capture_error = f"capture_timestamp_failed: {exc}"
                LOGGER.warning("Video capture timestamp unavailable; trying visual marker: %s", exc)
            else:
                self._video_time_offset_method = "capture_timestamp"
                self._video_timing_error = None
                return
        if not self._video_timing_marker_events or self._trajectory_start_epoch_ms is None:
            self._video_timing_error = (
                capture_error or self._video_timing_error
                or "marker_detection_failed: no synchronization markers"
            )
            return
        try:
            calibration = calibrate_video_offset(
                self.video_path,
                self._video_timing_marker_events,
                self._trajectory_start_epoch_ms,
                # Search through this recording's startup, allowing one second
                # for capture startup acknowledgement and frame rounding.
                search_duration_s=max(0.0, -(self._video_time_offset_s or 0.0)) + 1.0,
            )
        except Exception as exc:  # noqa: BLE001
            self._video_timing_error = f"marker_detection_failed: {exc}"
            if capture_error:
                self._video_timing_error = f"{capture_error}; {self._video_timing_error}"
            LOGGER.warning(
                "Video synchronization detection failed: %s",
                exc,
            )
            return
        self._video_time_offset_s = round(calibration.offset_s, 6)
        self._video_time_offset_method = "visual_marker"
        self._video_timing_error = None

    def _finalize_video_timeline(self) -> None:
        if self._video_time_offset_s is None:
            return
        pause_intervals: list[tuple[float, float]] = []
        if self._trajectory_start_epoch_ms is not None:
            pause_intervals = [
                (
                    (pause["start_epoch_ms"] - self._trajectory_start_epoch_ms) / 1000.0
                    - self._video_time_offset_s,
                    (pause["end_epoch_ms"] - self._trajectory_start_epoch_ms) / 1000.0
                    - self._video_time_offset_s,
                )
                for pause in self._inference_pauses
            ]
        try:
            self._video_time_offset_s = round(
                trim_video_to_trajectory_start(
                    self.video_path,
                    offset_s=self._video_time_offset_s,
                    bitrate=self.config.video_bitrate,
                    fps=self.config.video_fps,
                    duration_s=self._elapsed_t(),
                    pause_intervals=pause_intervals,
                ),
                6,
            )
        except Exception as exc:  # noqa: BLE001
            self._video_timing_error = f"video_trim_failed: {exc}"
            LOGGER.warning("Video timeline trim failed; retaining the original video: %s", exc)
            return
        method = self._video_time_offset_method or "timing_fallback"
        self._video_time_offset_method = f"{method}_trimmed"

    async def _stop_extension_video(self) -> None:
        controller = self._extension_controller_page
        if controller is None:
            await self._stop_force_cfr_dirty_loop()
            await self._restore_page_cursor()
            return
        try:
            await controller.evaluate("() => window.__stopRecording()")
            status = await self._wait_extension_status(
                controller,
                {"stopped", "error"},
                timeout_s=10.0,
            )
            if isinstance(status, dict) and status.get("state") == "error":
                raise RuntimeError(f"Extension video recorder failed while stopping: {status}")
            await controller.close()
            self._extension_controller_page = None
        finally:
            await self._stop_force_cfr_dirty_loop()
            await self._restore_page_cursor()

    def _probe_video(self) -> dict[str, Any] | None:
        if not self.video_path.exists():
            return None
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,bit_rate,nb_frames,nb_read_frames:format=duration,size,bit_rate",
                "-of",
                "json",
                str(self.video_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        payload = json.loads(result.stdout or "{}")
        if isinstance(payload, dict):
            try:
                duration = float(payload.get("format", {}).get("duration"))
            except (TypeError, ValueError):
                duration = None
            streams = payload.get("streams")
            if duration and isinstance(streams, list):
                for stream in streams:
                    if isinstance(stream, dict):
                        frames = stream.get("nb_read_frames")
                        if isinstance(frames, str) and frames.isdigit():
                            stream["computed_fps"] = round(int(frames) / duration, 3)
            return payload
        return None

    def _remux_video_container(self) -> None:
        temp_path = self.video_path.with_name("video.remux.webm")
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(self.video_path),
                "-c",
                "copy",
                str(temp_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to remux extension WebM: {result.stderr.strip()}")
        temp_path.replace(self.video_path)

    def _finalize_video_artifact(self) -> None:
        if not self.video_path.exists() or self.video_path.stat().st_size == 0:
            raise FileNotFoundError(f"Recorder did not write a video to {self.video_path}")
        if self.config.video_backend == "extension":
            self._remux_video_container()
        self._calibrate_video_time_offset()
        self._finalize_video_timeline()
        self._video_probe = self._probe_video()

    async def _save_interface_report(self, error: GameInterfaceError) -> None:
        path = system_report_path(self.config.output_root, self.episode_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "game_id": self.game_id, "task": self.task_id, "seed": self.config.random_seed,
            "source": "system", "phase": error.phase, "reason": str(error),
            "step": self._actor_steps, "state": None,
            "browser_errors": list(self.manager.browser_errors) if self.manager else [],
        }
        if self.manager and self.manager.page:
            try:
                report["state"] = await asyncio.wait_for(self.manager.get_game_state(), 2)
            except Exception as exc:
                report["state_error"] = str(exc)
            try:
                # The preparation overlay would hide the actual failed screen.
                await asyncio.wait_for(self.manager.page.evaluate(
                    "document.getElementById('__recordingGate')?.remove()"), 2)
                await asyncio.wait_for(self.manager.page.screenshot(
                    path=str(path.parent / "screen.png")), 2)
            except Exception as exc:
                report["screenshot_error"] = str(exc)
        for source in (self.inputs_path, self.states_path):
            if source.is_file():
                shutil.copyfile(source, path.parent / source.name)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        LOGGER.error("Environment interface report: %s", path)

    async def run(self) -> Path | None:
        self._validate_config()
        self._prepare_output_dir()
        if self.config.video_backend == "extension":
            self._ingest_server = ExtensionIngestServer(episode_dir=self.episode_dir)
            self._ingest_server.start()
        self._started_at = datetime.now().astimezone()
        self._stop_event = asyncio.Event()
        self._page_crashed = asyncio.Event()
        self._gameplay_ready = asyncio.Event()
        self._gameplay_ready.set()
        self._inference_pauses = []
        self._active_inference_pause = None
        self._install_signal_handlers()

        tasks: list[asyncio.Task[None]] = []
        pending_error: BaseException | None = None
        try:
            await self._start_browser()
            if self.manager and self.manager.page:
                self.manager.page.on("crash", self._on_page_crash)
            await self._install_input_recorder()
            if self.config.video_backend == "extension":
                await self._start_extension_video()
            await self._show_video_sync_marker()
            await self.manager.finish_recording_gate()
            if self.config.video_backend == "playwright" and self.config.force_cfr:
                # Playwright already writes a constant-rate stream. Keep the
                # real-time dirty heartbeat only through visual calibration;
                # leaving it active during gameplay can make CDP deliver more
                # source frames than the recorder's target rate and dilate the
                # headless video clock across repeated inference pauses.
                await self._stop_force_cfr_dirty_loop()
            if self.config.video_backend == "extension":
                await self._clear_input_buffers()
            self._clock_start = await self._page_clock()
            self._page_start_perf_ms = _safe_float(self._clock_start.get("perf_ms"))
            self._trajectory_start_epoch_ms = _safe_float(self._clock_start.get("epoch_ms"))
            self._python_monotonic_start = time.monotonic()
            self._set_video_time_offset_estimate()
            await self._reset_input_state("recording_start")
            self._write_summary()
            if self.manager and self.manager.page:
                await self.manager.page.bring_to_front()

            tasks.append(asyncio.create_task(self._input_loop()))
            if self.config.state_fps > 0:
                tasks.append(asyncio.create_task(self._state_loop()))

            wait_task = asyncio.create_task(self._wait_for_stop())
            crash_task = asyncio.create_task(self._page_crashed.wait())
            tasks.extend((wait_task, crash_task))
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
            for task in (wait_task, crash_task):
                if task in pending:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        except BaseException as exc:
            if isinstance(exc, GameInterfaceError):
                pending_error = exc
                self._outcome = "recording_error"
                self._stop_reason = type(exc).__name__
                self._error = str(exc)
                await self._save_interface_report(exc)
            elif self._page_crash_t is not None:
                pending_error = None
            elif _is_browser_closed_error(exc):
                pending_error = None
                self._mark_interrupted("browser_closed")
            elif isinstance(exc, KeyboardInterrupt):
                pending_error = None
                self._mark_interrupted("SIGINT")
            else:
                pending_error = exc
                self._outcome = "recording_error"
                self._stop_reason = exc.__class__.__name__
                self._error = "".join(traceback.format_exception(exc)).strip()
        finally:
            if self._stop_event:
                self._stop_event.set()
            self._restore_signal_handlers()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            if (not isinstance(pending_error, GameInterfaceError) and self.manager
                    and self.manager.page and self._page_alive()):
                try:
                    await self._drain_input_events()
                    await self._dispose_input_recorder()
                except Exception as exc:  # noqa: BLE001
                    if not _is_browser_closed_error(exc) and self._error is None:
                        self._error = "".join(traceback.format_exception(exc)).strip()

            if (
                self.config.video_backend == "extension"
                and self._extension_controller_page
            ):
                try:
                    await self._stop_extension_video()
                except Exception as exc:  # noqa: BLE001
                    if _is_browser_closed_error(exc) or self._is_interrupted():
                        if self._error is None:
                            self._error = "".join(traceback.format_exception(exc)).strip()
                    elif self._error is None:
                        self._error = "".join(traceback.format_exception(exc)).strip()
                    if pending_error is None and not self._is_interrupted():
                        pending_error = exc
                        self._outcome = "recording_error"
                        self._stop_reason = exc.__class__.__name__

            if (not isinstance(pending_error, GameInterfaceError) and self.manager
                    and self.manager.page and self.config.state_fps > 0 and self._page_alive()):
                try:
                    await self._sample_state(finalized=True)
                except Exception as exc:  # noqa: BLE001
                    if not _is_browser_closed_error(exc) and self._error is None:
                        self._error = "".join(traceback.format_exception(exc)).strip()

            self._python_monotonic_end = time.monotonic()
            self._ended_at = datetime.now().astimezone()

            if self.manager:
                if (not isinstance(pending_error, GameInterfaceError)
                        and self.config.video_backend == "playwright" and self._page_alive()):
                    try:
                        await self._stop_force_cfr_dirty_loop()
                    except Exception as exc:  # noqa: BLE001
                        if not _is_browser_closed_error(exc) and self._error is None:
                            self._error = "".join(traceback.format_exception(exc)).strip()
                self._blocked_external_requests = len(
                    self.manager.blocked_external_urls
                )
                await self.manager.close()
                self.manager = None
                try:
                    if not isinstance(pending_error, GameInterfaceError):
                        if self._playwright_video_path is not None:
                            shutil.copyfile(self._playwright_video_path, self.video_path)
                        self._finalize_video_artifact()
                except Exception as exc:  # noqa: BLE001
                    if self._error is None:
                        self._error = "".join(traceback.format_exception(exc)).strip()
                    if pending_error is None and not self._is_interrupted():
                        pending_error = exc
                        self._outcome = "recording_error"
                        self._stop_reason = exc.__class__.__name__
            self._write_summary()
            if self.launcher:
                self.launcher.stop()
                self.launcher = None
            if self._ingest_server:
                self._ingest_server.stop()
                self._ingest_server = None
            if self._browser_profile:
                self._browser_profile.cleanup()
                self._browser_profile = None
            if self._playwright_video_dir:
                self._playwright_video_dir.cleanup()
                self._playwright_video_dir = None

        if pending_error is not None:
            if self._python_monotonic_start is None:
                shutil.rmtree(self.episode_dir, ignore_errors=True)
                try:
                    self.episode_dir.parent.rmdir()
                except OSError:
                    pass
            raise pending_error
        validate_episode(self.episode_dir)
        LOGGER.info("Recorded trajectory at %s", self.episode_dir)
        return self.episode_dir


class FreeplayTrajectoryRecorder(TrajectoryRecorder):
    """Keep one game page open while recording manually controlled episodes."""

    def __init__(self, config: RecordingConfig):
        super().__init__(config)
        self._episode_lock = asyncio.Lock()
        self._recording_tasks: list[asyncio.Task[None]] = []
        self._control_server: FreeplayControlServer | None = None
        self._control_page: Any | None = None
        self._quit_event: asyncio.Event | None = None
        self._active_episode_dir: Path | None = None

    def _validate_config(self) -> None:
        super()._validate_config()
        if self.task is not None:
            raise ValueError("Freeplay recorder must be created without a task id.")
        if self.config.video_backend != "extension":
            raise ValueError("Human freeplay uses the extension video backend.")
        if self.config.control_port <= 0:
            raise ValueError("--control-port must be greater than 0.")

    def status(self) -> dict[str, Any]:
        return {
            "game": self.game_id,
            "game_url": self.game_url,
            "control_url": self._control_server.url if self._control_server else None,
            "recording": self._stop_event is not None and not self._stop_event.is_set(),
            "episode_id": self.episode_id if self._active_episode_dir else None,
            "episode_dir": str(self._active_episode_dir) if self._active_episode_dir else None,
            "outcome": self._outcome,
            "stop_reason": self._stop_reason,
            "error": self._error,
        }

    def _reset_episode_runtime(self) -> None:
        self._ingest_server = None
        self._extension_controller_page = None
        self._stop_event = None
        self._started_at = None
        self._ended_at = None
        self._python_monotonic_start = None
        self._python_monotonic_end = None
        self._page_start_perf_ms = None
        self._page_time_origin_ms = None
        self._trajectory_start_epoch_ms = None
        self._video_started_monotonic = None
        self._video_time_offset_s = None
        self._video_time_offset_method = None
        self._video_timing_marker_events = []
        self._video_timing_error = None
        self._video_probe = None
        self._clock_start = None
        self._raw_input_index = 0
        self._state_sample_index = 0
        self._valid_state_samples = 0
        self._valid_policy_state_samples = 0
        self._last_progress = None
        self._last_task_status = None
        self._actor_steps = 0
        self._reset_count = 0
        self._reset_in_progress = False
        self._eval_metrics = {}
        self._stop_deadline = None
        self._stop_reason = None
        self._outcome = "unknown"
        self._error = None
        self._recording_tasks = []
        self._active_episode_dir = None
        self._inference_pauses = []
        self._active_inference_pause = None

    async def _open_control_page(self) -> None:
        if self.manager is None or self.manager.context is None:
            raise RuntimeError("Browser context is unavailable.")
        if self._control_server is None:
            raise RuntimeError("Control server is unavailable.")
        self._control_page = await self.manager.context.new_page()
        await self._control_page.goto(self._control_server.url)
        if self.manager.page is not None:
            await self.manager.page.bring_to_front()

    def _install_quit_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        def schedule_quit(reason: str) -> None:
            asyncio.create_task(self.quit("interrupted", reason))

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, schedule_quit, sig.name)
                self._loop_signal_handlers.append(sig)
            except (NotImplementedError, RuntimeError):
                self._previous_signal_handlers[sig] = signal.getsignal(sig)

                def request_quit(signum, _frame, *, stop_signal=sig) -> None:
                    reason = signal.Signals(signum).name if signum else stop_signal.name
                    loop.call_soon_threadsafe(schedule_quit, reason)

                signal.signal(sig, request_quit)

    async def _watch_game_page_close(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            return
        await page.wait_for_event("close")
        await self.quit("interrupted", "browser_closed")

    async def start_episode(self) -> dict[str, Any]:
        async with self._episode_lock:
            if self._stop_event is not None and not self._stop_event.is_set():
                return self.status()

            self._reset_episode_runtime()
            self._set_episode_paths(self._build_episode_id())
            self._prepare_output_dir()
            self._ingest_server = ExtensionIngestServer(episode_dir=self.episode_dir)
            self._ingest_server.start()
            self._started_at = datetime.now().astimezone()
            self._stop_event = asyncio.Event()

            try:
                await self.manager.begin_recording_gate()
                await self._install_input_recorder()
                await self._start_extension_video()
                await self._show_video_sync_marker()
                await self.manager.finish_recording_gate()
                await self._clear_input_buffers()
                self._clock_start = await self._page_clock()
                self._page_start_perf_ms = _safe_float(self._clock_start.get("perf_ms"))
                self._trajectory_start_epoch_ms = _safe_float(
                    self._clock_start.get("epoch_ms")
                )
                self._python_monotonic_start = time.monotonic()
                self._set_video_time_offset_estimate()
                await self._reset_input_state("recording_start")
                self._write_summary()
                if self.manager and self.manager.page:
                    await self.manager.page.bring_to_front()

                self._recording_tasks = [asyncio.create_task(self._input_loop())]
                if self.config.state_fps > 0:
                    self._recording_tasks.append(asyncio.create_task(self._state_loop()))
                self._active_episode_dir = self.episode_dir
                return self.status()
            except Exception:
                await self._cleanup_failed_episode()
                raise

    async def _cleanup_failed_episode(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        for task in self._recording_tasks:
            task.cancel()
        await asyncio.gather(*self._recording_tasks, return_exceptions=True)
        if self._extension_controller_page is not None:
            await self._stop_extension_video()
        if self._ingest_server is not None:
            self._ingest_server.stop()
            self._ingest_server = None

    async def stop_episode(self, outcome: str = "manual", reason: str = "control_panel") -> dict[str, Any]:
        async with self._episode_lock:
            if self._stop_event is None or self._stop_event.is_set():
                return self.status()

            self._request_stop(outcome, reason)
            for task in self._recording_tasks:
                task.cancel()
            await asyncio.gather(*self._recording_tasks, return_exceptions=True)
            self._recording_tasks = []

            if self.manager and self.manager.page:
                await self._drain_input_events()
                await self._dispose_input_recorder()

            await self._stop_extension_video()
            if self.config.state_fps > 0:
                await self._sample_state(finalized=True)

            self._python_monotonic_end = time.monotonic()
            self._ended_at = datetime.now().astimezone()

            if self._ingest_server is not None:
                self._ingest_server.stop()
            self._finalize_video_artifact()
            self._write_summary()
            validate_episode(self.episode_dir)
            self._ingest_server = None
            self._active_episode_dir = None
            return {
                **self.status(),
                "episode_id": self.episode_id,
                "episode_dir": str(self.episode_dir),
            }

    async def quit(self, outcome: str = "interrupted", reason: str = "quit") -> dict[str, Any]:
        if self._stop_event is not None and not self._stop_event.is_set():
            await self.stop_episode(outcome, reason)
        if self._quit_event is not None:
            self._quit_event.set()
        return self.status()

    async def run(self) -> Path | None:
        self._validate_config()
        loop = asyncio.get_running_loop()
        self._quit_event = asyncio.Event()
        self._install_quit_signal_handlers()
        self._control_server = FreeplayControlServer(
            recorder=self,
            loop=loop,
            port=self.config.control_port,
        )
        self._control_server.start()
        page_close_task: asyncio.Task[None] | None = None
        try:
            await self._start_browser(
                wait_actionable=False,
                show_recording_gate=False,
            )
            page_close_task = asyncio.create_task(self._watch_game_page_close())
            await self._open_control_page()
            print(f"Game opened: {self.game_id}")
            print(f"URL: {self.game_url}")
            print(f"Recorder control panel: {self._control_server.url}")
            if self.manager and self.manager.page and not self.manager.page.is_closed():
                await self.manager.page.bring_to_front()
            await self._quit_event.wait()
        except asyncio.CancelledError:
            if self._quit_event is not None:
                self._quit_event.set()
            raise
        finally:
            await self._shutdown_freeplay(page_close_task)
        return None

    async def _shutdown_freeplay(self, page_close_task: asyncio.Task[None] | None) -> None:
        """Always exit cleanly on Quit / Ctrl+C / browser close."""
        self._restore_signal_handlers()
        if page_close_task is not None:
            page_close_task.cancel()
            await asyncio.gather(page_close_task, return_exceptions=True)
        try:
            if self._stop_event is not None and not self._stop_event.is_set():
                await self.stop_episode("interrupted", "shutdown")
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("stop_episode during shutdown skipped: %s", exc)
        try:
            if self._control_page is not None and not self._control_page.is_closed():
                await self._control_page.close()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("control page close skipped: %s", exc)
        finally:
            self._control_page = None
        try:
            if self.manager:
                await self.manager.close()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("browser manager close skipped: %s", exc)
        finally:
            self.manager = None
        try:
            if self.launcher:
                self.launcher.stop()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("launcher stop skipped: %s", exc)
        finally:
            self.launcher = None
        try:
            if self._control_server:
                self._control_server.stop()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("control server stop skipped: %s", exc)
        finally:
            self._control_server = None
        try:
            if self._browser_profile:
                self._browser_profile.cleanup()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("browser profile cleanup skipped: %s", exc)
        finally:
            self._browser_profile = None
