"""Playwright-facing helpers for the Unity WebGL bridge."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .profile import validate_profile


BRIDGE_PATH = Path(__file__).with_name("bridge.js")


@lru_cache(maxsize=1)
def bridge_script() -> str:
    return BRIDGE_PATH.read_text(encoding="utf-8").strip()


async def configure_page(page: Page, profile: dict[str, Any]) -> dict[str, Any]:
    checked = validate_profile(profile)
    result = await page.evaluate(
        "profile => {"
        " if (!window.__unityBridge) throw new Error('Unity bridge was not installed');"
        " return window.__unityBridge.configure(profile);"
        "}",
        checked,
    )
    return result if isinstance(result, dict) else {}


async def page_status(page: Page) -> dict[str, Any] | None:
    result = await page.evaluate(
        "() => window.__unityBridge ? window.__unityBridge.status() : null"
    )
    return result if isinstance(result, dict) else None


def configure_script(profile: dict[str, Any]) -> str:
    """Build a standalone script for diagnostics outside BrowserGameManager."""
    checked = validate_profile(profile)
    payload = json.dumps(checked, ensure_ascii=False, separators=(",", ":"))
    return f"window.__unityBridge.configure({payload});"


__all__ = ["BRIDGE_PATH", "bridge_script", "configure_page", "configure_script", "page_status"]
