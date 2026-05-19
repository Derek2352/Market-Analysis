"""Tests for the pure-Python parts of src/render/core.py.

These don't drive Playwright, so they run on every pytest invocation.
"""
from __future__ import annotations

import re

from src.render.core import accent_palette, truncate


# ---------------------------------------------------------------------------
# Deterministic accent palette
# ---------------------------------------------------------------------------


def test_accent_palette_is_deterministic_per_persona_id() -> None:
    a1 = accent_palette("persona_a4f9c7e2")
    a2 = accent_palette("persona_a4f9c7e2")
    assert a1 == a2


def test_accent_palette_distinguishes_different_personas() -> None:
    a = accent_palette("persona_a4f9c7e2")
    b = accent_palette("persona_b7e2da40")
    # Different ids should land on different hues — at least one channel must differ.
    assert a != b


def test_accent_palette_returns_valid_hex_strings() -> None:
    a = accent_palette("persona_x")
    hex_re = re.compile(r"^#[0-9a-f]{6}$")
    assert hex_re.match(a.from_)
    assert hex_re.match(a.via)
    assert hex_re.match(a.to)


# ---------------------------------------------------------------------------
# CJK-safe truncate
# ---------------------------------------------------------------------------


def test_truncate_short_string_unchanged() -> None:
    text, was = truncate("hello", 100)
    assert text == "hello"
    assert was is False


def test_truncate_adds_ellipsis_when_too_long() -> None:
    text, was = truncate("a" * 200, 50)
    assert was is True
    assert text.endswith("…")
    assert len(text) == 50


def test_truncate_handles_cjk_codepoints_safely() -> None:
    cjk = "用咗呢個 app 好多年, 個介面真係好難用" * 5
    text, was = truncate(cjk, 30)
    assert was is True
    assert text.endswith("…")
    # Each CJK char is one code point — slicing won't produce mojibake.
    assert "�" not in text
