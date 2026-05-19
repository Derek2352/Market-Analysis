"""Shared fixtures + skip-guards for the Phase 8 render tests.

These tests drive Playwright against a real Chromium build, which isn't
installed by default. Tests skip cleanly when the binary isn't present —
they're the kind of thing you run locally after `playwright install
chromium`, not on every CI tick.
"""
from __future__ import annotations

import os
import shutil
import pytest


def _chromium_available() -> bool:
    env_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env_path and os.path.exists(env_path):
        return True
    # Fall back to the standard Playwright cache location.
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    # Best-effort check: ask Playwright to find chromium without launching.
    # An exception here means the browsers aren't installed.
    try:
        from playwright._impl._driver import compute_driver_executable  # noqa: F401
    except Exception:
        return False
    cache = os.path.expanduser("~/.cache/ms-playwright")
    if os.path.isdir(cache):
        for entry in os.listdir(cache):
            if entry.startswith("chromium"):
                return True
    return False


@pytest.fixture(scope="session", autouse=True)
def _skip_if_no_chromium() -> None:
    if not _chromium_available():
        pytest.skip(
            "Chromium for Playwright not available. Run "
            "`playwright install chromium` or set "
            "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH.",
            allow_module_level=True,
        )


def pytest_collection_modifyitems(config, items) -> None:
    """Mark all render tests slow so users can deselect with `pytest -m 'not slow'`."""
    slow = pytest.mark.slow
    for item in items:
        item.add_marker(slow)
