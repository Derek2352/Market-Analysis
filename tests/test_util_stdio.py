"""Smoke test for the Windows UTF-8 stdio shim.

Reproduces the failure mode the user saw on Windows PowerShell: emitting
a → / ⚠ to a cp1252-backed stdout raises UnicodeEncodeError. We verify
force_utf8_stdio() switches a cp1252 stream to utf-8 with replacement,
and that a no-op call on an already-utf-8 stream doesn't break anything.
"""
from __future__ import annotations

import io

from src.util_stdio import force_utf8_stdio


class _Cp1252Stream(io.TextIOWrapper):
    """A TextIOWrapper whose underlying codec is cp1252, like a fresh Windows console."""

    def __init__(self) -> None:
        super().__init__(io.BytesIO(), encoding="cp1252", errors="strict", newline="")


def test_cp1252_stream_initially_cant_encode_arrow():
    """Baseline — without the shim, writing → on cp1252 raises."""
    s = _Cp1252Stream()
    try:
        s.write("→ ⚠ ✓")
        s.flush()
    except UnicodeEncodeError:
        return
    raise AssertionError("cp1252 stream unexpectedly tolerated → — test premise broken")


def test_force_utf8_stdio_switches_to_utf8(monkeypatch):
    s = _Cp1252Stream()
    # Pretend this is sys.stdout for the duration of the call.
    monkeypatch.setattr("sys.stdout", s)

    force_utf8_stdio()

    # After reconfigure, → is fine.
    s.write("→ ⚠ ✓\n")
    s.flush()
    assert "utf" in s.encoding.lower().replace("-", "")


def test_force_utf8_stdio_is_idempotent_on_utf8_stream(monkeypatch):
    """Already-UTF-8 streams (Linux, macOS, modern Windows Terminal) stay untouched."""
    s = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    monkeypatch.setattr("sys.stdout", s)
    # Should not raise, should not error out.
    force_utf8_stdio()
    s.write("→ already utf-8\n")
    s.flush()


def test_force_utf8_stdio_tolerates_streams_without_reconfigure(monkeypatch):
    """Some streams (pytest capture buffers) don't expose reconfigure — must be a no-op."""

    class _NoReconfigure:
        encoding = "cp1252"

        def write(self, s):
            return len(s)

        def flush(self):
            pass

    monkeypatch.setattr("sys.stdout", _NoReconfigure())
    force_utf8_stdio()  # must not raise
