"""One bad source must not abort the whole API run.

Mirrors the CLI's per-source try/except: when a single SourceScraper.search()
raises, the API pipeline logs scrape.source.error, emits an SSE event with
that name, and proceeds to the next source. The run only hard-fails if
every source raised and zero posts came back.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

from src.api.models import RunRequest
from src.api.pipeline import _scrape_step
from src.api.runs import RunState
from src.schemas.raw import RawPost


def _post(source: str, region: str, n: int) -> RawPost:
    return RawPost(
        id=f"{source}_{n}",
        source=source,
        source_category="forums",
        region=region,
        language="en",
        url=f"https://example.com/{source}/{n}",
        author_hash="0" * 64,
        title=f"post {n}",
        body=f"body {n}",
        posted_at=datetime.now(timezone.utc),
        signal_type="opinion",
    )


class _GoodScraper:
    """Yields three posts then stops."""

    def search(self, query: str, *, since, limit: int) -> Iterator[RawPost]:
        yield _post("good", "HK", 1)
        yield _post("good", "HK", 2)
        yield _post("good", "HK", 3)


class _BadScraper:
    """Raises mid-stream after emitting one post."""

    def search(self, query: str, *, since, limit: int) -> Iterator[RawPost]:
        yield _post("bad", "HK", 1)
        raise RuntimeError("simulated 403 / parse failure")


class _ExplodingScraper:
    """Raises before yielding anything."""

    def search(self, query: str, *, since, limit: int) -> Iterator[RawPost]:
        raise ConnectionError("dns lookup failed")
        yield  # unreachable, keeps mypy happy about generator typing


def _make_state(tmp_path: Path, sources: list[str], topic: str = "test topic") -> RunState:
    request = RunRequest(
        topic=topic,
        region="HK",
        sources=sources,
        since_days=30,
        limit_per_source=50,
        provider="anthropic",
    )
    return RunState.create(tmp_path / "runs", "20260101T000000Z", request)


def _captured_events(state: RunState) -> list[tuple[str, dict]]:
    """Read what got broadcast on the EventLog."""
    return [(e.type, e.data) for e in state.events.history()]


def test_one_bad_source_does_not_abort_the_run(tmp_path, monkeypatch):
    """`good` produces posts, `bad` raises mid-stream; run keeps going."""
    def fake_get_scraper(source_id, **_kwargs):
        if source_id == "good":
            return _GoodScraper()
        if source_id == "bad":
            return _BadScraper()
        raise KeyError(source_id)

    monkeypatch.setattr("src.api.pipeline.get_scraper", fake_get_scraper)
    # Both source ids must be in the implemented set for the pipeline not to drop them.
    monkeypatch.setattr(
        "src.api.pipeline.available_sources",
        lambda: ["good", "bad"],
    )

    state = _make_state(tmp_path, ["good", "bad"])
    total = _scrape_step(state, tmp_path)

    # `good` emits 3 + `bad` emits 1 before raising = 4.
    assert total == 4

    events = _captured_events(state)
    types = [t for t, _ in events]
    assert "scrape.source.error" in types, f"missing per-source error event in {types}"
    err = next(d for t, d in events if t == "scrape.source.error")
    assert err["source"] == "bad"
    assert "simulated 403" in err["error"]
    # The stage still reports done — the run continues to embed.
    done = next(d for t, d in events if t == "stage_done" and d.get("stage") == "scrape")
    assert "1 source(s) failed" in done["message"]
    assert done["failed_sources"][0]["source"] == "bad"


def test_all_sources_failing_hard_fails_the_stage(tmp_path, monkeypatch):
    """If every source raises and total=0, surface that as a real failure."""
    monkeypatch.setattr(
        "src.api.pipeline.get_scraper",
        lambda source_id, **_kw: _ExplodingScraper(),
    )
    monkeypatch.setattr(
        "src.api.pipeline.available_sources",
        lambda: ["bad1", "bad2"],
    )

    state = _make_state(tmp_path, ["bad1", "bad2"])
    with pytest.raises(RuntimeError, match=r"All 2 source\(s\) failed"):
        _scrape_step(state, tmp_path)

    # Both per-source errors should still have been broadcast.
    err_sources = sorted(
        d["source"] for t, d in _captured_events(state) if t == "scrape.source.error"
    )
    assert err_sources == ["bad1", "bad2"]


def test_partial_success_does_not_raise(tmp_path, monkeypatch):
    """One good source + one fully-broken source = run continues, no raise."""
    def fake_get_scraper(source_id, **_kwargs):
        if source_id == "good":
            return _GoodScraper()
        return _ExplodingScraper()

    monkeypatch.setattr("src.api.pipeline.get_scraper", fake_get_scraper)
    monkeypatch.setattr(
        "src.api.pipeline.available_sources",
        lambda: ["good", "boom"],
    )

    state = _make_state(tmp_path, ["good", "boom"])
    total = _scrape_step(state, tmp_path)

    assert total == 3  # only `good`'s posts
    assert any(
        t == "scrape.source.error" and d["source"] == "boom"
        for t, d in _captured_events(state)
    )
