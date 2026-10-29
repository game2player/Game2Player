"""Headless browser helpers for running HTML5 games in Playwright."""

from __future__ import annotations

import asyncio
import base64
import ctypes.util
import hashlib
import json
import logging
import os
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Optional
from urllib.parse import urlparse

from PIL import Image
from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Error as PlaywrightError,
    Page,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)
from tools.paths import temporary_dir
from tools.unity_webgl.runtime import bridge_script, configure_page, configure_script, page_status
from .game_state_tracker import (    INIT_GAME_API_SCRIPT,
    PAUSE_GAME_SCRIPT,
    PRESERVE_WEBGL_DRAWING_BUFFER_SCRIPT,
    RESET_GAME_API_SCRIPT,
    RESUME_GAME_SCRIPT,
    GET_GAME_STATE_SCRIPT,
)

# Shared readiness ceilings for recording and Builder validation. A reload-based
# reset can cost as much as a cold start; readiness returns immediately once usable.
COLD_START_TIMEOUT_S = 60.0
RESET_TIMEOUT_S = 60.0


LOGGER = logging.getLogger(__name__)


class GameInterfaceError(RuntimeError):
    """A loaded game's initialization or reset did not become usable."""

    def __init__(self, phase: str, message: str):
        self.phase = phase
        super().__init__(message)

DEFAULT_GOTO_TIMEOUT_MS = 60000
DEFAULT_LOAD_STATE_TIMEOUT_MS = 5000
DEFAULT_READINESS_POLL_S = 0.3
BROWSER_SCRIPT_DIR = Path(__file__).with_name("browser_scripts")


# WebGL on an NVIDIA GPU: ANGLE's Vulkan backend, the one whose device can be chosen.
GPU_WEBGL_ARGS = ("--use-angle=vulkan", "--enable-features=Vulkan")
# Chromium reaches the NVIDIA driver through libglvnd.
GLVND_LIBRARIES = ("libEGL.so.1", "libGLdispatch.so.0")
# Set, it turns gpu_webgl on for every browser launched by a process and its children
# (a campaign's games, rollout workers, the coding agents' own runs): a PCI bus id as
# nvidia-smi prints it picks the card, "any" leaves the choice to the driver.
WEBGL_GPU_ENV = "GAMEENV_WEBGL_GPU"


def _webgl_gpu() -> str | None:
    return os.environ.get(WEBGL_GPU_ENV, "").strip() or None


def gpu_webgl_env(device: str | None = None, *, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment in which headless Chromium renders WebGL on an NVIDIA GPU (Linux).

    Without libglvnd Chromium's GPU process cannot start and WebGL falls back to the
    CPU. A system that lacks libEGL.so.1 borrows libglvnd from this Python environment
    (conda ships it), exposed alone in a directory of its own so that nothing else the
    browser loads changes. `device` is a PCI bus id as nvidia-smi prints it
    ("00000000:43:00.0"); identical cards are told apart only by it, through the
    device-select Vulkan layer (DRI_PRIME). None leaves the choice to the driver.
    """
    if not sys.platform.startswith("linux"):
        raise ValueError("gpu_webgl is supported on Linux only")
    env = dict(os.environ if base is None else base)
    if ctypes.util.find_library("EGL") is None:
        source = Path(sys.prefix) / "lib"
        missing = [name for name in GLVND_LIBRARIES if not (source / name).exists()]
        if missing:
            raise RuntimeError(f"gpu_webgl needs libglvnd on the system or in {sys.prefix}; missing {missing}")
        borrowed = temporary_dir("browser") / f"gameenv-glvnd-{os.getuid()}"
        borrowed.mkdir(exist_ok=True)
        for name in GLVND_LIBRARIES:
            try:
                (borrowed / name).symlink_to(source / name)
            except FileExistsError:
                pass
        env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (str(borrowed), env.get("LD_LIBRARY_PATH"))))
        # The borrowed loader looks for driver descriptions under its own prefix.
        env.setdefault("__EGL_VENDOR_LIBRARY_DIRS", "/usr/share/glvnd/egl_vendor.d:/etc/glvnd/egl_vendor.d")
    if device:
        domain, bus, slot = device.strip().lower().split(":")
        env["DRI_PRIME"] = f"pci-{domain[-4:]}_{bus}_{slot.replace('.', '_')}"
    return env


def _load_browser_script(filename: str) -> str:
    return (BROWSER_SCRIPT_DIR / filename).read_text(encoding="utf-8").strip()


def _build_dynamic_speed_control_script(initial_speed_multiplier: float) -> str:
    return _load_browser_script("dynamic_speed_control.js").replace(
        "__INITIAL_SPEED_MULTIPLIER__",
        json.dumps(initial_speed_multiplier),
    )


def _build_deterministic_random_script(seed: int) -> str:
    return _load_browser_script("deterministic_random.js").replace(
        "__RANDOM_SEED__",
        json.dumps(seed),
    )


def _default_screenshot_dir() -> Path:
    return temporary_dir("screenshots") / f"{os.getpid()}_{uuid.uuid4().hex}"


def _extension_id_from_manifest(extension_dir: Path) -> str | None:
    manifest_path = extension_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    key = json.loads(manifest_path.read_text(encoding="utf-8")).get("key")
    if not isinstance(key, str) or not key:
        return None
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(ch, 16)) for ch in digest)


# Closing a page or detaching from it never completes once its renderer has crashed.
CLOSE_STEP_TIMEOUT_S = 10.0


@dataclass(slots=True)
class ScreenshotConfig:
    width: int
    height: int
    screenshot_dir: Path


class CDPScreenshotter:
    """Capture and normalize screenshots through a persistent CDP session."""

    def __init__(self, config: ScreenshotConfig):
        self.config = config
        self._cdp_session: CDPSession | None = None

    async def capture(
        self,
        *,
        context: BrowserContext | None,
        page: Page | None,
        name: str,
        new_cdp_session: Callable[[], Awaitable[CDPSession]],
    ) -> Path:
        target = self.config.screenshot_dir / name
        if not context or not page:
            raise RuntimeError("Browser page is not initialized.")

        if self._cdp_session is None:
            self._cdp_session = await new_cdp_session()

        result = await self._cdp_session.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": False,
                "fromSurface": True,
            },
        )
        screenshot_data = self._normalize_size(base64.b64decode(result["data"]))
        target.write_bytes(screenshot_data)
        return target

    def _normalize_size(self, data: bytes) -> bytes:
        target_size = (self.config.width, self.config.height)
        with Image.open(BytesIO(data)) as image:
            if image.size == target_size:
                return data

            normalized = image.resize(target_size, resample=Image.Resampling.NEAREST)
            output = BytesIO()
            normalized.save(output, format="PNG")
            return output.getvalue()

    async def close(self) -> None:
        if not self._cdp_session:
            return
        try:
            # A session on a crashed page never answers the detach.
            await asyncio.wait_for(self._cdp_session.detach(), timeout=CLOSE_STEP_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("CDP detach skipped: %s", exc)
        finally:
            self._cdp_session = None


class BrowserReadinessGate:
    """Wait until a browser game reaches an actionable status."""

    @staticmethod
    def normalize_status(state: dict | None) -> str | None:
        if not isinstance(state, dict):
            return None
        raw_status = state.get("status")
        if not isinstance(raw_status, str):
            return None
        status = raw_status.strip().lower()
        return status or None

    @staticmethod
    def normalize_actionable(state: dict | None) -> bool | None:
        if not isinstance(state, dict) or "is_actionable" not in state:
            return None
        return state.get("is_actionable") is True

    async def wait_until_actionable(
        self,
        *,
        stage: str,
        timeout_s: float,
        actionable_statuses: tuple[str, ...],
        get_state: Callable[[], Awaitable[dict | None]],
        extra_wait_after_actionable_s: float = 0.1,
    ) -> bool:
        desired = {
            status.strip().lower() for status in actionable_statuses if isinstance(status, str)
        }
        if not desired:
            desired = {"playing"}

        started_at = time.monotonic()
        last_status: str | None = None
        last_actionable: bool | None = None

        while True:
            state = await get_state()
            status = self.normalize_status(state)
            actionable = self.normalize_actionable(state)

            if status != last_status or actionable != last_actionable:
                LOGGER.info(
                    "Game readiness (%s): status=%s is_actionable=%s",
                    stage,
                    status or "unavailable",
                    actionable,
                )
                last_status = status
                last_actionable = actionable

            ready = actionable if actionable is not None else status in desired
            if ready:
                LOGGER.info(
                    "Game readiness (%s): ready with status=%s is_actionable=%s after %.2fs",
                    stage,
                    status,
                    actionable,
                    time.monotonic() - started_at,
                )
                await asyncio.sleep(extra_wait_after_actionable_s)
                return True

            elapsed = time.monotonic() - started_at
            if elapsed >= timeout_s:
                LOGGER.warning(
                    "Game readiness (%s): timeout after %.2fs "
                    "(last status=%s, is_actionable=%s, desired=%s)",
                    stage,
                    elapsed,
                    status or "unavailable",
                    actionable,
                    sorted(desired),
                )
                return False

            await asyncio.sleep(DEFAULT_READINESS_POLL_S)


@dataclass
class BrowserConfig:
    """Configuration values for launching the browser."""

    game_url: str
    width: int = 1280
    height: int = 720
    headless: bool = False
    speed_multiplier: float = 1.0
    screenshot_dir: Path = field(default_factory=_default_screenshot_dir)
    random_seed: int | None = 42
    game_api_init_options: Mapping[str, object] = field(default_factory=dict)
    zoom_level: float = 1.0
    record_video_dir: Path | None = None
    record_video_size: tuple[int, int] | None = None
    extension_dir: Path | None = None
    user_data_dir: Path | None = None
    unity_profile: dict[str, object] | None = None
    enable_unity_bridge: bool = False
    block_external_network: bool = False
    show_recording_gate: bool = False
    page_init_scripts: tuple[str, ...] = ()
    # Some native introductions take longer than the default readiness budget.
    readiness_timeout_s: float | None = None
    # Render WebGL on an NVIDIA GPU instead of the CPU (Linux; see gpu_webgl_env).
    # A 3D game then takes well under a core instead of several. Off unless
    # $GAMEENV_WEBGL_GPU is set.
    gpu_webgl: bool = field(default_factory=lambda: _webgl_gpu() is not None)
    # The card, by PCI bus id as nvidia-smi prints it; None lets the driver choose.
    gpu_device: str | None = field(
        default_factory=lambda: None if _webgl_gpu() in (None, "any") else _webgl_gpu())


class BrowserGameManager:
    """Launch a Chromium instance and prepare an HTML5 game session."""

    def __init__(self, config: BrowserConfig):
        self.config = config
        self._playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.video_started_monotonic: float | None = None
        self._readiness = BrowserReadinessGate()
        self.blocked_external_urls: list[str] = []
        self.browser_errors: list[str] = []
        # close() only removes a screenshot dir this manager created itself.
        self._owns_screenshot_dir = False
        self._screenshotter = CDPScreenshotter(
            ScreenshotConfig(
                width=config.width,
                height=config.height,
                screenshot_dir=config.screenshot_dir,
            )
        )

    async def __aenter__(self) -> "BrowserGameManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self, *, wait_actionable: bool = False) -> None:
        """Launch Playwright and navigate to the configured game URL."""
        self._owns_screenshot_dir = not self.config.screenshot_dir.exists()
        self.config.screenshot_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        await self._launch_browser()
        self.page.on("pageerror", lambda error: self._record_browser_error(str(error)))
        self.page.on("console", lambda message: self._record_browser_error(message.text)
                     if message.type == "error" else None)
        await self._install_page_scripts()
        await self._navigate_to_game()
        async def initialize():
            await self._maybe_configure_unity_profile()
            await self._maybe_init_game_api()

        budget = self.config.readiness_timeout_s or COLD_START_TIMEOUT_S
        await self._prepare_interface(
            "init", initialize(), wait_actionable=wait_actionable,
            timeout_s=max(0.01, budget - (time.monotonic() - started)),
        )

    def _record_browser_error(self, message: str) -> None:
        self.browser_errors.append(message)
        del self.browser_errors[:-20]

    async def _prepare_interface(
        self, phase: str, operation: Awaitable, *, wait_actionable: bool, timeout_s: float,
    ):
        try:
            async with asyncio.timeout(timeout_s):
                result = await operation
                if wait_actionable:
                    if result is False or not await self.wait_until_actionable(
                        stage=phase, timeout_s=timeout_s,
                    ):
                        raise GameInterfaceError(phase, f"Game {phase} did not reach an actionable state")
                return result
        except TimeoutError as exc:
            raise GameInterfaceError(
                phase, f"Game {phase} did not complete within its startup/reset time limit"
            ) from exc

    @staticmethod
    def _browser_launch_args() -> list[str]:
        # Prefer GPU + keep renderer awake (WSL/WSLg often blocklists GPU otherwise).
        return [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
            "--ignore-gpu-blocklist",
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--js-flags=--max-old-space-size=8192",
        ]

    async def _launch_browser(self) -> None:
        self._playwright = await async_playwright().start()
        launch_args = self._browser_launch_args()
        launch_env = None
        if self.config.gpu_webgl:
            launch_args.extend(GPU_WEBGL_ARGS)
            launch_env = gpu_webgl_env(self.config.gpu_device)
        if self.config.extension_dir is not None:
            resolved_extension_dir = self.config.extension_dir.resolve()
            extension_dir = str(resolved_extension_dir)
            extension_id = _extension_id_from_manifest(resolved_extension_dir)
            launch_args.extend(
                [
                    f"--disable-extensions-except={extension_dir}",
                    f"--load-extension={extension_dir}",
                ]
            )
            if extension_id:
                launch_args.append(f"--allowlisted-extension-id={extension_id}")
            if self.config.user_data_dir is None:
                raise RuntimeError("BrowserConfig.user_data_dir is required when loading an extension.")
            self.context = await self._playwright.chromium.launch_persistent_context(
                str(self.config.user_data_dir),
                headless=self.config.headless,
                args=launch_args,
                env=launch_env,
                viewport={"width": self.config.width, "height": self.config.height},
                service_workers="allow",
                device_scale_factor=1,
            )
            self.browser = self.context.browser
            await self._install_network_policy()
            self.page = await self.context.new_page()
            return

        self.browser = await self._playwright.chromium.launch(
            headless=self.config.headless,
            args=launch_args,
            env=launch_env,
        )
        context_options = {
            "viewport": {"width": self.config.width, "height": self.config.height},
            "service_workers": "block",
            "device_scale_factor": 1,
        }
        if self.config.record_video_dir is not None:
            context_options["record_video_dir"] = str(self.config.record_video_dir)
            video_size = self.config.record_video_size or (self.config.width, self.config.height)
            context_options["record_video_size"] = {
                "width": int(video_size[0]),
                "height": int(video_size[1]),
            }
        self.context = await self.browser.new_context(**context_options)
        await self._install_network_policy()
        if self.config.record_video_dir is not None:
            self.video_started_monotonic = time.monotonic()
        self.page = await self.context.new_page()

        if self.config.zoom_level != 1.0:
            cdp_session = await self._new_cdp_session()
            try:
                await cdp_session.send(
                    "Emulation.setPageScaleFactor",
                    {"pageScaleFactor": self.config.zoom_level},
                )
            finally:
                await cdp_session.detach()

    async def _install_network_policy(self) -> None:
        """Optionally block every request except local/runtime-owned resources."""
        if not self.config.block_external_network or self.context is None:
            return

        async def route_request(route: Route) -> None:
            url = route.request.url
            parsed = urlparse(url)
            local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            if local or parsed.scheme in {"about", "blob", "data", "chrome-extension"}:
                await route.continue_()
                return
            self.blocked_external_urls.append(url)
            await route.abort("blockedbyclient")

        await self.context.route("**/*", route_request)

    async def _install_page_scripts(self) -> None:
        if not self.page:
            raise RuntimeError("Browser page is not initialized.")

        await self.page.add_init_script(PRESERVE_WEBGL_DRAWING_BUFFER_SCRIPT)
        await self.page.add_init_script(_load_browser_script("no_fullscreen.js"))
        if self.config.block_external_network:
            await self.page.add_init_script(
                _load_browser_script("offline_network.js")
            )
        if self.config.show_recording_gate:
            await self.page.add_init_script(
                _load_browser_script("recording_gate.js")
            )
        if self.config.unity_profile is not None:
            await self.page.add_init_script(
                f"{bridge_script()}\n{configure_script(self.config.unity_profile)}"
            )
        elif self.config.enable_unity_bridge:
            await self.page.add_init_script(bridge_script())
        await self.page.add_init_script(
            _build_dynamic_speed_control_script(self.config.speed_multiplier)
        )
        if self.config.random_seed is not None:
            await self.page.add_init_script(
                _build_deterministic_random_script(self.config.random_seed)
            )
        for script in self.config.page_init_scripts:
            if script.strip():
                await self.page.add_init_script(script)

    async def _navigate_to_game(self) -> None:
        if not self.page:
            raise RuntimeError("Browser page is not initialized.")

        goto_timeout_ms = int(
            os.environ.get("GAMEENV_PAGE_GOTO_TIMEOUT_MS", str(DEFAULT_GOTO_TIMEOUT_MS))
        )
        try:
            await self.page.goto(
                self.config.game_url,
                wait_until="domcontentloaded",
                timeout=goto_timeout_ms,
            )
        except PlaywrightError as exc:
            raise RuntimeError(f"Failed to open game URL {self.config.game_url}: {exc}") from exc

        try:
            await self.page.wait_for_load_state("load", timeout=DEFAULT_LOAD_STATE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            LOGGER.debug(
                "Page load-state=load timed out after DOM ready: %s",
                self.config.game_url,
            )

    async def _new_cdp_session(self) -> CDPSession:
        if not self.context or not self.page:
            raise RuntimeError("Browser page is not initialized.")
        return await self.context.new_cdp_session(self.page)

    async def _maybe_init_game_api(self) -> None:
        if not self.page:
            return
        try:
            options = dict(self.config.game_api_init_options)
            if self.config.random_seed is not None:
                options["seed"] = self.config.random_seed
            await self.page.evaluate(INIT_GAME_API_SCRIPT, options)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("gameAPI init failed: %s", exc)

    async def _maybe_configure_unity_profile(self) -> None:
        if not self.page or self.config.unity_profile is None:
            return
        try:
            status = await page_status(self.page)
            if not status or status.get("profileGameId") != self.config.unity_profile.get("game_id"):
                status = await configure_page(self.page, self.config.unity_profile)
            LOGGER.info("Unity WebGL bridge configured: %s", status)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "Unity WebGL state bridge is unavailable; gameplay will continue without it: %s",
                exc,
            )

    async def capture_screenshot(self, name: str) -> Path:
        """Capture a screenshot without triggering viewport flash in headed mode."""
        return await self._screenshotter.capture(
            context=self.context,
            page=self.page,
            name=name,
            new_cdp_session=self._new_cdp_session,
        )

    async def get_game_state(self) -> Optional[dict]:
        if not self.page:
            return None
        try:
            state = await self.page.evaluate(GET_GAME_STATE_SCRIPT)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Failed to read game state from gameAPI: %s", exc)
            return None
        return state if isinstance(state, dict) else None

    async def wait_until_actionable(
        self,
        stage: str,
        timeout_s: float = 60.0,
        actionable_statuses: tuple[str, ...] = ("ready", "playing"),
        extra_wait_after_actionable_s: float = 0.1,
    ) -> bool:
        """Wait until game status is actionable before agent interaction starts."""
        return await self._readiness.wait_until_actionable(
            stage=stage,
            timeout_s=timeout_s,
            actionable_statuses=actionable_statuses,
            get_state=self.get_game_state,
            extra_wait_after_actionable_s=extra_wait_after_actionable_s,
        )

    async def reset_game(self, *, wait_actionable: bool = False) -> bool:
        """Reset game state via gameAPI without reloading the page."""
        if not self.page:
            return False
        async def reset():
            try:
                return bool(await self.page.evaluate(RESET_GAME_API_SCRIPT, self.config.random_seed))
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("gameAPI reset failed: %s", exc)
                return False

        return bool(await self._prepare_interface(
            "reset", reset(), wait_actionable=wait_actionable,
            timeout_s=self.config.readiness_timeout_s or RESET_TIMEOUT_S,
        ))

    async def begin_recording_gate(self) -> None:
        """Cover the current page while a recording boundary is prepared."""
        if not self.page:
            return
        await self.page.evaluate(
            """() => {
                try {
                    window.sessionStorage.setItem(
                        '__recordingGateState',
                        'armed'
                    );
                } catch (_) {}
            }"""
        )
        await self.page.evaluate(_load_browser_script("recording_gate.js"))

    async def finish_recording_gate(self) -> None:
        """Reveal the game and wait for the uncovered frame to be painted."""
        if not self.page:
            return
        await self.page.evaluate(
            """async () => {
                try {
                    window.sessionStorage.setItem(
                        '__recordingGateState',
                        'released'
                    );
                } catch (_) {}
                document.getElementById('__recordingGate')?.remove();
                await new Promise(resolve =>
                    (window.__realSetTimeout || window.setTimeout)(resolve, 50)
                );
            }"""
        )

    async def pause_game(self) -> dict[str, object] | None:
        """Pause the game by freezing time-based hooks in the page."""
        if not self.page:
            return None
        try:
            result = await self.page.evaluate(PAUSE_GAME_SCRIPT)
            LOGGER.debug("Pause: %s", result)
            return result if isinstance(result, dict) else None
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Pause hook failed: %s", exc)
            return None

    async def resume_game(self) -> dict[str, object] | None:
        """Resume the game after pausing."""
        if not self.page:
            return None
        try:
            result = await self.page.evaluate(RESUME_GAME_SCRIPT)
            LOGGER.debug("Resume: %s", result)
            return result if isinstance(result, dict) else None
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Resume hook failed: %s", exc)
            return None

    async def close(self) -> None:
        """Gracefully close browser resources and temporary screenshots."""
        await self._screenshotter.close()

        try:
            if self.page:
                try:
                    await asyncio.wait_for(self.page.close(), timeout=CLOSE_STEP_TIMEOUT_S)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.debug("Page close skipped: %s", exc)
        finally:
            self.page = None

        try:
            if self.context:
                try:
                    await self.context.close()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.debug("Context close skipped: %s", exc)
        finally:
            self.context = None

        try:
            if self.browser:
                try:
                    await self.browser.close()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.debug("Browser close skipped: %s", exc)
        finally:
            self.browser = None

        try:
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.debug("Playwright stop skipped: %s", exc)
        finally:
            self._playwright = None

        try:
            self._remove_screenshot_dir()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Failed to clean screenshot dir %s: %s", self.config.screenshot_dir, exc)

    def _remove_screenshot_dir(self) -> None:
        """Delete the screenshot dir, but only when this manager owns it.

        A caller that points screenshot_dir at a directory it wants to keep used
        to have that whole tree deleted here, so a pre-existing directory is now
        left alone and the frames inside it are removed one by one instead.
        """
        target = self.config.screenshot_dir
        if not target.exists():
            return
        resolved = target.resolve()
        if resolved == Path.cwd().resolve() or resolved in Path.cwd().resolve().parents:
            LOGGER.warning("Refusing to clean screenshot dir %s: it contains the working tree", resolved)
            return
        if self._owns_screenshot_dir:
            shutil.rmtree(target, ignore_errors=True)
            return
        LOGGER.debug("Screenshot dir %s pre-existed; removing captured frames only", resolved)
        for frame in target.glob("*.png"):
            frame.unlink(missing_ok=True)


__all__ = ["BrowserConfig", "BrowserGameManager"]
