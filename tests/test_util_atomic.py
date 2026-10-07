"""atomic_write_json round-trip + Windows-style retry behaviour."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.util_atomic import atomic_write_json


def test_round_trip_basic(tmp_path: Path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1, "b": "中文", "c": [1, 2]})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"a": 1, "b": "中文", "c": [1, 2]}


def test_unicode_round_trip_ensure_ascii_false(tmp_path: Path):
    """CJK characters survive verbatim, not as \\uXXXX escapes."""
    p = tmp_path / "cjk.json"
    atomic_write_json(p, {"name": "支付寶 香港"})
    raw = p.read_text(encoding="utf-8")
    assert "支付寶 香港" in raw
    assert "\\u" not in raw


def test_no_tmp_file_left_behind(tmp_path: Path):
    p = tmp_path / "x.json"
    atomic_write_json(p, [1, 2, 3])
    assert p.exists()
    assert not p.with_suffix(".json.tmp").exists()


def test_compact_separators(tmp_path: Path):
    """`separators=(',', ':')` produces the no-whitespace form needed for big caches."""
    p = tmp_path / "compact.json"
    atomic_write_json(p, {"a": 1, "b": 2}, indent=None, separators=(",", ":"))
    assert p.read_text(encoding="utf-8") == '{"a":1,"b":2}'


def test_retries_on_permission_error_then_succeeds(tmp_path: Path):
    """Simulate the Windows race: replace() raises PermissionError, then succeeds."""
    p = tmp_path / "raced.json"
    p.write_text("{}", encoding="utf-8")  # destination exists

    call_count = {"n": 0}
    original_replace = Path.replace

    def flaky_replace(self, target):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return original_replace(self, target)

    with patch.object(Path, "replace", flaky_replace):
        atomic_write_json(p, {"ok": True}, initial_delay=0.001)

    assert call_count["n"] >= 3  # 2 failures + 1 success
    assert json.loads(p.read_text(encoding="utf-8")) == {"ok": True}


def test_gives_up_after_exhausting_retries(tmp_path: Path):
    """If every retry fails, propagate the PermissionError."""
    p = tmp_path / "stuck.json"

    def always_fails(self, target):
        raise PermissionError(5, "Access is denied")

    with patch.object(Path, "replace", always_fails):
        with pytest.raises(PermissionError):
            atomic_write_json(p, {"x": 1}, retries=3, initial_delay=0.001)

    # Tmp file should not remain.
    assert not p.with_suffix(".json.tmp").exists()


def test_writes_to_pre_existing_destination(tmp_path: Path):
    """The whole point of atomic write — overwriting must work."""
    p = tmp_path / "x.json"
    atomic_write_json(p, {"v": 1})
    atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text(encoding="utf-8")) == {"v": 2}
