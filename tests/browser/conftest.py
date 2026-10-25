"""The page-test seam: Chromium (Playwright) against the app served locally.

These tests drive the real display page against a real server holding fixture
photos, and assert DOM state and geometry - not pixels. They are the one place
the Album visit's JavaScript is exercised, since this project has no JS unit
harness. CI runs them after the Python suite (.github/workflows/tests.yml).

**They skip cleanly when no browser is available**, so the plain suite still
passes on a box without one. Set `REQUIRE_BROWSER=1` (CI does) to make a missing
Playwright or Chromium a failure instead of a skip, so the step can never go
quietly green by not running. `PLAYWRIGHT_CHROMIUM_EXECUTABLE` points at a
system Chrome/Chromium when Playwright's own download is not installed.

The server runs in a thread of the test process (lifespan off, so no scheduler
and no sync): the tests prepare the data directory with the app's own modules
exactly like the other suites, then start serving it.
"""
from __future__ import annotations

import os
import socket
import threading
import time

import pytest
from PIL import Image

from app import config_store, paths
from app.main import app

REQUIRE = os.environ.get("REQUIRE_BROWSER") == "1"


def _unavailable(reason: str):
    if REQUIRE:
        pytest.fail(f"browser tests are required but {reason}", pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("Playwright is not installed")
    pw = sync_playwright().start()
    try:
        b = pw.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            args=["--no-sandbox"])
    except Exception as exc:  # noqa: BLE001 - any launch failure means no usable browser
        pw.stop()
        _unavailable(f"no Chromium could be launched ({str(exc).splitlines()[0][:120]})")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    pg = ctx.new_page()
    pg.set_default_timeout(20_000)
    yield pg
    ctx.close()


@pytest.fixture
def serve():
    """Start the app on a free port; returns the base URL. Stopped after the test."""
    import uvicorn
    running = []

    def start() -> str:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started, "the test server did not start"
        running.append((server, thread))
        return f"http://127.0.0.1:{port}"

    yield start
    for server, thread in running:
        server.should_exit = True
        thread.join(10)


# ---- the data the tests show ---------------------------------------------------------

@pytest.fixture
def venue(write_tap):
    """A small venue: two Manual taps (two one-tap pages), a logo and a ticker."""
    write_tap("custom", 1, name="Hazy IPA", abv=6.2)
    write_tap("custom", 2, name="Red Ale", abv=5.0)
    Image.new("RGB", (200, 80), (40, 120, 200)).save(paths.DATA_DIR / "venue_logo.png")
    config_store.update_config(
        num_taps=2, paginate=True, page_size=1, rotation_seconds=3,
        venue_logo="venue_logo.png", venue_logo_height_vh=10,
        announcement_text="Ticker text")
