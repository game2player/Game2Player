"""Game state tracking helpers and shared gameAPI scripts for browser games."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable

from playwright.async_api import Frame, Page


LOGGER = logging.getLogger(__name__)

EXCLUDE_FROM_SUMMARY = {"raw", "timestampMs", "schemaVersion"}
NO_GAME_STATE_SUMMARY = "(no game state)"

GET_GAME_ID_SCRIPT = """
() => {
    const api = window.gameAPI;
    if (!api || typeof api.getState !== "function") return null;
    const state = api.getState();
    return state && state.gameId ? state.gameId : null;
}
"""

GET_GAME_STATE_SCRIPT = """
() => {
    const api = window.gameAPI;
    if (!api) return null;
    if (typeof api.getState === "function") return api.getState();
    return null;
}
"""

GET_POLICY_STATE_BUNDLE_SCRIPT = """
() => {
    let state = null;
    let policyState = null;
    try {
        const api = window.gameAPI;
        const value = api && typeof api.getState === "function" ? api.getState() : null;
        if (value && typeof value === "object" && !Array.isArray(value)) state = value;
    } catch (_) {}
    try {
        const probe = window.__policyProbe;
        const value = probe && typeof probe.getState === "function"
            ? probe.getState()
            : null;
        if (value && typeof value === "object" && !Array.isArray(value)) {
            policyState = value;
        }
    } catch (_) {}
    return {state: state, policy_state: policyState};
}
"""

# Both pass the run's seed. A page-level `Math.random` replacement does not reach a
# game that draws from its own seeded generator -- most packaged games hook
# `Math.seedrandom` and keep their own current seed -- so the seed has to arrive
# through the API the game exposes for it. Initialization may also carry task
# options such as the initial level requested by a task.
INIT_GAME_API_SCRIPT = """
async (options) => {
    const api = window.gameAPI;
    if (!api) return;
    if (typeof api.init === "function") {
        await api.init(options);
    }
}
"""

RESET_GAME_API_SCRIPT = """
async (seed) => {
    const api = window.gameAPI;
    if (!api || typeof api.reset !== "function") return false;
    await api.reset(seed === null ? {} : {seed: seed});
    return true;
}
"""

FOCUS_PAGE_SCRIPT = """
() => {
    try { window.focus && window.focus(); } catch (e) {}
    try { document && document.body && document.body.focus && document.body.focus(); } catch (e) {}
    try {
        const canvas = document && document.querySelector ? document.querySelector("canvas") : null;
        if (canvas && canvas.focus) canvas.focus();
    } catch (e) {}
}
"""

PAUSE_GAME_SCRIPT = """() => {
    if (window.__pauseGame) {
        window.__pauseGame();
        const s = window.__getGameSpeedState ? window.__getGameSpeedState() : {};
        return {
            ok: true,
            realEpochMs: window.__realDateNow ? window.__realDateNow() : Date.now(),
            totalPaused: s.totalPausedTime
        };
    }
    return { ok: false };
}"""

RESUME_GAME_SCRIPT = """() => {
    if (window.__resumeGame) {
        const realEpochMs = window.__realDateNow ? window.__realDateNow() : Date.now();
        window.__resumeGame();
        const s = window.__getGameSpeedState ? window.__getGameSpeedState() : {};
        return { ok: true, realEpochMs, totalPaused: s.totalPausedTime };
    }
    return { ok: false };
}"""

PRESERVE_WEBGL_DRAWING_BUFFER_SCRIPT = """(function() {
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attrs) {
        if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") {
            attrs = Object.assign({}, attrs || {}, { preserveDrawingBuffer: true });
        }
        return origGetContext.call(this, type, attrs);
    };
})();"""


@dataclass
class GameStateSnapshot:
    """Captured game state payload plus a concise summary."""

    state: dict | None
    summary: str


class GameStateTracker:
    """Base interface for game-state capture implementations."""

    name = "base"

    async def capture(self, page: Page | None) -> dict | None:
        raise NotImplementedError

    def _strip_nulls(self, value: object) -> object:
        if isinstance(value, dict):
            cleaned: dict = {}
            for key, item in value.items():
                cleaned_item = self._strip_nulls(item)
                if cleaned_item is None:
                    continue
                cleaned[key] = cleaned_item
            return cleaned or None
        if isinstance(value, list):
            cleaned_items = []
            for item in value:
                cleaned_item = self._strip_nulls(item)
                if cleaned_item is None:
                    continue
                cleaned_items.append(cleaned_item)
            return cleaned_items or None
        return value

    def _build_summary_state(self, state: dict | None) -> dict | None:
        if not state or not isinstance(state, dict):
            return None

        summary: dict = {}
        for key, value in state.items():
            if key in EXCLUDE_FROM_SUMMARY:
                continue
            cleaned_value = self._strip_nulls(value)
            if cleaned_value is None:
                continue
            summary[key] = cleaned_value
        return summary or None

    def summarize(self, state: dict | None) -> str:
        """Create summary by including all fields except internal metadata.

        This is adaptive - any new field added to a game API automatically
        appears in the summary without manual configuration.
        """
        summary = self._build_summary_state(state)
        if not summary:
            return NO_GAME_STATE_SUMMARY
        return json.dumps(summary, ensure_ascii=False)

    async def snapshot(self, page: Page | None) -> GameStateSnapshot:
        state = await self.capture(page)
        raw_state = state if isinstance(state, dict) else None
        return GameStateSnapshot(state=raw_state, summary=self.summarize(raw_state))


class GameAPIStateTracker(GameStateTracker):
    """Capture game state from window.gameAPI.getState()."""

    name = "game_api"

    async def _evaluate_state(self, page: Page | Frame) -> dict | None:
        try:
            state = await page.evaluate(GET_GAME_STATE_SCRIPT)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Game state capture failed: %s", exc)
            return None
        if not isinstance(state, dict):
            return None
        return state

    @staticmethod
    def _candidate_pages(page: Page) -> Iterable[Page | Frame]:
        yield page
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            yield frame

    async def capture(self, page: Page | None) -> dict | None:
        if not page:
            return None

        for candidate in self._candidate_pages(page):
            state = await self._evaluate_state(candidate)
            if state:
                return state

        return None


class PolicyStateBundleTracker(GameAPIStateTracker):
    """Atomically capture trusted and policy-only state from the same frame."""

    name = "policy_bundle"

    async def _evaluate_state(self, page: Page | Frame) -> dict | None:
        try:
            bundle = await page.evaluate(GET_POLICY_STATE_BUNDLE_SCRIPT)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Policy state bundle capture failed: %s", exc)
            return None
        return bundle if isinstance(bundle, dict) else None

    async def capture(self, page: Page | None) -> dict | None:
        if not page:
            return None
        fallback = None
        for candidate in self._candidate_pages(page):
            bundle = await self._evaluate_state(candidate)
            if not isinstance(bundle, dict):
                continue
            if isinstance(bundle.get("state"), dict):
                return bundle
            if fallback is None and isinstance(bundle.get("policy_state"), dict):
                fallback = bundle
        return fallback


def build_game_state_tracker() -> GameStateTracker:
    """Factory for selecting a game-state tracker."""
    return GameAPIStateTracker()


def build_policy_state_bundle_tracker() -> GameStateTracker:
    return PolicyStateBundleTracker()


__all__ = [
    "FOCUS_PAGE_SCRIPT",
    "GET_GAME_ID_SCRIPT",
    "GET_POLICY_STATE_BUNDLE_SCRIPT",
    "GET_GAME_STATE_SCRIPT",
    "GameAPIStateTracker",
    "GameStateSnapshot",
    "GameStateTracker",
    "INIT_GAME_API_SCRIPT",
    "NO_GAME_STATE_SUMMARY",
    "PAUSE_GAME_SCRIPT",
    "PRESERVE_WEBGL_DRAWING_BUFFER_SCRIPT",
    "RESET_GAME_API_SCRIPT",
    "RESUME_GAME_SCRIPT",
    "PolicyStateBundleTracker",
    "build_game_state_tracker",
    "build_policy_state_bundle_tracker",
]
