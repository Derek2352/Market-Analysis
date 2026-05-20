"""UTF-8 stdio for Windows.

Windows PowerShell defaults to cp1252 ("charmap") which can't encode
many characters this codebase emits (→ ⚠ ✓ … plus CJK in scraper
output and personas). Without this shim a single arrow in a log line
crashes uvicorn / the CLI mid-stream with ``UnicodeEncodeError``.

We call ``reconfigure(encoding="utf-8", errors="replace")`` on every
entry point. ``errors="replace"`` ensures a glyph the terminal font
itself can't render becomes ``?`` rather than aborting the process.

No-op on POSIX systems and on terminals already serving UTF-8.
"""
from __future__ import annotations

import sys


def force_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        # Only reconfigure if the current encoding isn't already UTF-8.
        # Skipping the call on Linux/macOS avoids spuriously changing
        # the error handler on a stream the user might have set up.
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc != "utf8":
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # Some streams (pytest capture, redirected pipes) refuse
                # reconfigure; that's fine — they're already byte-safe.
                pass
