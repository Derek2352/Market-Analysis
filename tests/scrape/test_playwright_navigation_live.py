"""PlaywrightManager against a real Chromium and a local HTTP server.

Pins the browser behaviour the status handling relies on: an error status
with an empty body makes page.goto raise (net::ERR_HTTP_RESPONSE_CODE_FAILURE),
a page's own script can reload it after goto returned, and the "response"
event still reports both. Skipped when no Chromium is available.
"""
from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.scrape.base.http import ForbiddenError
from src.scrape.base.playwright import PlaywrightManager


def _chromium_available() -> bool:
    env_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env_path:
        return os.path.exists(env_path)
    for cache in (os.path.expanduser("~/.cache/ms-playwright"),
                  os.path.expanduser("~/AppData/Local/ms-playwright")):
        if os.path.isdir(cache) and any(e.startswith("chromium") for e in os.listdir(cache)):
            return True
    return False


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not _chromium_available(), reason="Chromium for Playwright not available"),
]

_PAGES = {
    "/ok": (200, "<html><body><p id='ok'>server text</p>"
                 "<script>document.getElementById('ok').textContent = 'changed by script'</script>"
                 "</body></html>"),
    "/empty403": (403, ""),
    "/challenge": (200, "<html><body>checking…<script>setTimeout(function () "
                        "{ location.replace('/denied'); }, 50)</script></body></html>"),
    "/denied": (403, "<html><body>Access Denied</body></html>"),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server API
        status, body = _PAGES.get(self.path, (404, "not found"))
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


class _AllowAll:
    def allowed(self, url, user_agent="*"):
        return True

    def denial_reason(self, url):
        return ""


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def pw():
    manager = PlaywrightManager(robots_cache=_AllowAll(), rate=0)
    yield manager
    manager.close()


def test_server_html_is_what_the_server_sent_not_the_live_dom(pw, base_url) -> None:
    with pw.get_page(f"{base_url}/ok") as page:
        pw.navigate(page, f"{base_url}/ok", wait_until="load")
        pw.ensure_ok(page, f"{base_url}/ok")
        assert "changed by script" in page.content()
        assert "server text" in pw.document_html(page)


def test_empty_body_403_raises_forbidden(pw, base_url) -> None:
    with pw.get_page(f"{base_url}/empty403") as page, pytest.raises(ForbiddenError, match="403"):
        pw.navigate(page, f"{base_url}/empty403")


def test_403_on_a_script_reload_is_caught_by_ensure_ok(pw, base_url) -> None:
    with pw.get_page(f"{base_url}/challenge") as page:
        pw.navigate(page, f"{base_url}/challenge", wait_until="domcontentloaded")
        page.wait_for_url("**/denied", timeout=10_000)
        page.wait_for_load_state("domcontentloaded")
        with pytest.raises(ForbiddenError, match="403"):
            pw.ensure_ok(page, f"{base_url}/challenge")
