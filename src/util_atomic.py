"""Atomic JSON file write, with Windows retry.

On POSIX, ``Path.replace`` is an atomic rename. On Windows the underlying
``MoveFileExW`` raises ``PermissionError [WinError 5]`` if any handle is
currently open on the destination — including a reader in another thread
or process. That happens routinely here: FastAPI handlers read
``run.json`` while the pipeline writes it. The race is small (a few μs)
but real, and the cost of a crashed run is high.

``atomic_write_json`` writes the payload to a sibling ``.tmp`` file then
renames into place, retrying the rename for up to ~1s on Windows-style
``PermissionError``. POSIX takes the fast path on the first try.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    default: Any = str,
    separators: tuple[str, str] | None = None,
    retries: int = 10,
    initial_delay: float = 0.01,
) -> None:
    """Write ``payload`` to ``path`` atomically.

    On Windows the final rename retries on PermissionError (reader holds
    the destination) with exponential backoff. Total max wait ≈ 1s.

    ``indent``/``separators`` pass straight through to ``json.dumps``.
    Use ``indent=None, separators=(",", ":")`` for compact output (e.g.
    a large embedding cache).
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            payload,
            indent=indent,
            ensure_ascii=ensure_ascii,
            default=default,
            separators=separators,
        ),
        encoding="utf-8",
    )

    delay = initial_delay
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            tmp.replace(path)
            return
        except PermissionError as e:
            # Windows: a concurrent reader of `path` blocks the rename.
            # POSIX never raises here, so this branch is effectively
            # win32-only in practice.
            last_exc = e
            time.sleep(delay)
            delay = min(delay * 2, 0.2)
    # Final attempt outside the loop — if it still fails, propagate.
    try:
        tmp.replace(path)
    except PermissionError:
        # Best-effort cleanup of the orphan tmp file.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if last_exc is not None:
            raise last_exc
        raise
