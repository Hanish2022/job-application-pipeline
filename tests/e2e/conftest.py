from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live(tmp_path):
    """A fresh seeded server per test, so tests can freely change statuses/profile."""
    port = _free_port()
    log = (tmp_path / "server.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "e2e" / "server.py"), str(tmp_path / "e2e.db"), str(port)],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError(f"e2e server exited early:\n{(tmp_path / 'server.log').read_text()}")
            try:
                urllib.request.urlopen(url + "/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("e2e server did not start")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture()
def app(page, live):
    """Open the dashboard; fail the test if the page logs a console error or throws."""
    errors: list[str] = []
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.set_default_timeout(8000)
    page.set_viewport_size({"width": 1366, "height": 900})
    page.goto(live + "/")
    page.wait_for_selector("[data-testid=job]")
    page.base = live  # convenience for tests that navigate again
    page.console_errors = errors
    yield page
    assert errors == [], f"browser errors: {errors}"


@pytest.fixture()
def cold(page, live):
    """The Cold-email page (vendored yc-outreach inside our shell). Fails the test on unexpected console errors."""
    errors: list[str] = []
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.set_default_timeout(10000)
    page.set_viewport_size({"width": 1366, "height": 900})
    page.goto(live + "/outreach")
    expect(page.locator("#load")).to_be_enabled()          # batches finished loading (locator waits don't need eval, which the page's CSP forbids)
    page.base = live
    page.console_errors = errors
    yield page
    assert errors == [], f"browser errors: {errors}"
