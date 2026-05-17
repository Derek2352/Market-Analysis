from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.scrape.utils.since import parse_since


def _close(a: datetime, b: datetime, tol_seconds: int = 5) -> bool:
    return abs((a - b).total_seconds()) < tol_seconds


def test_parses_days() -> None:
    expected = datetime.now(timezone.utc) - timedelta(days=90)
    assert _close(parse_since("90d"), expected)


def test_parses_hours() -> None:
    expected = datetime.now(timezone.utc) - timedelta(hours=24)
    assert _close(parse_since("24h"), expected)


def test_parses_weeks_and_months() -> None:
    expected_w = datetime.now(timezone.utc) - timedelta(weeks=2)
    expected_m = datetime.now(timezone.utc) - timedelta(days=180)
    assert _close(parse_since("2w"), expected_w)
    assert _close(parse_since("6m"), expected_m)


def test_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        parse_since("nope")
    with pytest.raises(ValueError):
        parse_since("90")
    with pytest.raises(ValueError):
        parse_since("d90")
