"""_slugify must dodge Windows reserved device names.

Windows refuses to create files or directories whose stem is one of:
CON, PRN, AUX, NUL, COM1-9, LPT1-9 (case-insensitive). If a user runs
`mkt scrape --topic "CON"`, the unsuffixed slug "con" would crash with
OSError [WinError 123] on `data/raw/con/HK/...mkdir`.
"""
from __future__ import annotations

import pytest

from src.api.pipeline import _slugify as _api_slugify
from src.cli import _slugify as _cli_slugify

_SLUGIFIERS = [_api_slugify, _cli_slugify]


@pytest.mark.parametrize("slugify", _SLUGIFIERS)
@pytest.mark.parametrize(
    "topic",
    ["CON", "con", "PRN", "aux", "NUL", "COM1", "com9", "LPT1", "lpt9"],
)
def test_reserved_names_get_suffixed(slugify, topic):
    out = slugify(topic)
    assert out not in {
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    # The original token is still visible to humans.
    assert topic.lower() in out


@pytest.mark.parametrize("slugify", _SLUGIFIERS)
def test_normal_topics_unaffected(slugify):
    assert slugify("MTR Mobile") == "mtr_mobile"
    assert slugify("AlipayHK") == "alipayhk"
    assert slugify("支付寶 香港") == "untitled"  # CJK strips out, sane fallback


@pytest.mark.parametrize("slugify", _SLUGIFIERS)
def test_only_reserved_word_substring_is_left_alone(slugify):
    """`Connect` contains `con` but isn't the device name."""
    assert slugify("Connect") == "connect"
    # COM10+ is not reserved on Windows — only COM1..COM9.
    assert slugify("COM10") == "com10"
