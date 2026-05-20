"""Windows launcher — boots the FastAPI backend + Next.js standalone UI
side by side, opens the user's default browser, and shuts both down
cleanly on exit.

Layout this expects (matches what build_windows.bat produces):

  MarketAnalytics/
    MarketAnalytics.exe          ← this script, frozen by PyInstaller
    _internal/                   ← PyInstaller's "folder of stuff"
      (frozen Python + deps)
      node/
        node.exe                 ← portable Node distribution
      ui/
        server.js                ← Next.js standalone server
        .next/
        public/
      src/                       ← FastAPI app (read-only)
    data/                        ← writable: runs, personas, journeys, renders

When frozen by PyInstaller, ``sys._MEIPASS`` points at ``_internal/``;
in dev (``python scripts/win_launcher.py``) it falls back to the repo
root, so the same script drives both code paths.
"""
from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional


# ── Path discovery ─────────────────────────────────────────────────────


def _bundle_root() -> Path:
    """Find the folder containing the bundled UI + Python deps.

    Frozen build: PyInstaller exposes ``sys._MEIPASS``. One-dir mode puts
    everything under ``_internal/`` next to the .exe.
    Dev run:      walk up from this script to the repo root.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


BUNDLE = _bundle_root()
UI_DIR = BUNDLE / "ui"
NODE_DIR = BUNDLE / "node"

# In dev the user has Node on PATH; in the frozen build we use the
# portable Node that build_windows.bat downloaded into ``node/``.
NODE_EXE = NODE_DIR / ("node.exe" if os.name == "nt" else "node")


# ── Port helpers ───────────────────────────────────────────────────────


def _port_is_free(port: int) -> bool:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port(preferred: int) -> int:
    """Use ``preferred`` if free, else let the OS allocate one."""
    if _port_is_free(preferred):
        return preferred
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 30.0) -> bool:
    """Poll ``url`` until it answers or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.25)
    return False


# ── Subprocess management ──────────────────────────────────────────────


class ProcessGroup:
    """A small lifecycle manager for the API + Node subprocesses.

    Holding both here keeps shutdown logic in one place — Ctrl+C in the
    console, a fatal launcher error, or the user closing the console all
    flow through ``.terminate_all()``.
    """

    def __init__(self) -> None:
        self.procs: list[subprocess.Popen[bytes]] = []

    def spawn(self, *args, **kwargs) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(*args, **kwargs)
        self.procs.append(proc)
        return proc

    def terminate_all(self, timeout: float = 5.0) -> None:
        for p in self.procs:
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        deadline = time.monotonic() + timeout
        for p in self.procs:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                p.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    p.kill()


# ── API + UI process starters ──────────────────────────────────────────


def start_fastapi(group: ProcessGroup, port: int) -> subprocess.Popen[bytes]:
    """Spawn uvicorn serving ``src.api.app:app``.

    Uses ``sys.executable`` so frozen builds drive the bundled Python
    runtime instead of looking for a system Python.
    """
    env = os.environ.copy()
    # Default the author-hash salt if it isn't set — keeps the app usable
    # out of the box. A real install should edit data/.env or set this
    # explicitly before launching.
    env.setdefault(
        "AUTHOR_HASH_SALT",
        env.get("AUTHOR_HASH_SALT", "default-marketanalytics-launcher-salt"),
    )
    # Make sure the bundle root is on sys.path so ``src.api.app`` imports
    # regardless of how Python was launched. CWD is not always added
    # automatically (and never under PyInstaller).
    pythonpath = str(BUNDLE)
    if env.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = pythonpath
    cmd = [
        sys.executable, "-m", "uvicorn",
        "src.api.app:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "warning",
    ]
    return group.spawn(cmd, cwd=str(BUNDLE), env=env)


def start_nextjs(group: ProcessGroup, port: int, api_port: int) -> subprocess.Popen[bytes]:
    """Spawn the Next.js standalone server.

    ``server.js`` honors HOSTNAME and PORT env vars. We pin both so the
    Next.js process binds where we expect.
    """
    if not (UI_DIR / "server.js").exists():
        raise SystemExit(
            f"Next.js bundle missing at {UI_DIR / 'server.js'} — "
            f"did `npm run build` run in ui/ ?"
        )

    # Pick the node binary. Frozen build: bundled Node. Dev: PATH.
    if NODE_EXE.exists():
        node = str(NODE_EXE)
    else:
        # No bundled Node and no PATH — bail with a clear message.
        node = "node"

    env = os.environ.copy()
    env["HOSTNAME"] = "127.0.0.1"
    env["PORT"] = str(port)
    # The UI reads NEXT_PUBLIC_API_BASE at build time, not runtime; the
    # build script pins it to http://127.0.0.1:8000. If a non-default API
    # port is in use, the UI still falls back to that build-time value.
    env.setdefault("NEXT_PUBLIC_API_BASE", f"http://127.0.0.1:{api_port}")
    return group.spawn([node, "server.js"], cwd=str(UI_DIR), env=env)


# ── Console UX ────────────────────────────────────────────────────────


def _say(msg: str) -> None:
    """Print to stderr so PyInstaller's --windowed mode doesn't swallow it."""
    print(msg, file=sys.stderr, flush=True)


def _install_signal_handlers(group: ProcessGroup, stop_event: threading.Event) -> None:
    def _handler(signum, frame):  # noqa: ARG001
        _say("\nshutting down …")
        stop_event.set()
    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    api_port = _pick_port(8000)
    ui_port = _pick_port(3000)

    group = ProcessGroup()
    stop_event = threading.Event()
    _install_signal_handlers(group, stop_event)

    try:
        _say("Market Analytics — launching")
        _say(f"  api:  http://127.0.0.1:{api_port}")
        _say(f"  ui:   http://127.0.0.1:{ui_port}")
        _say("")

        api = start_fastapi(group, api_port)
        ui = start_nextjs(group, ui_port, api_port)

        api_ready = _wait_for_http(f"http://127.0.0.1:{api_port}/health", timeout=30)
        ui_ready  = _wait_for_http(f"http://127.0.0.1:{ui_port}/",         timeout=30)
        if not api_ready:
            _say("api never came up — check console logs above")
            return 1
        if not ui_ready:
            _say("ui never came up — check console logs above")
            return 1

        _say("ready · opening browser · Ctrl+C in this window to stop")
        webbrowser.open(f"http://127.0.0.1:{ui_port}/")

        # Block until a subprocess dies, or the user interrupts.
        while not stop_event.is_set():
            for p in (api, ui):
                if p.poll() is not None:
                    _say(f"subprocess {p.args!r} exited with code {p.returncode}")
                    stop_event.set()
                    break
            stop_event.wait(timeout=0.5)
        return 0
    finally:
        group.terminate_all()


if __name__ == "__main__":
    sys.exit(main())
