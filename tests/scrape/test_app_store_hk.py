from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from src.scrape.app_store_hk import AppStoreHKScraper
from src.scrape.base import SourceError


def _load(fixtures_dir: str, name: str) -> dict:
    return json.loads(Path(fixtures_dir, name).read_text())


def _search_pat() -> re.Pattern[str]:
    return re.compile(r"^https://itunes\.apple\.com/search.*")


def _reviews_pat(app_id: str, page: int) -> re.Pattern[str]:
    return re.compile(
        rf"^https://itunes\.apple\.com/hk/rss/customerreviews/"
        rf"page={page}/id={app_id}/sortby=mostrecent/json.*"
    )


def test_search_parses_reviews_from_page1(
    httpx_mock: HTTPXMock, fixtures_dir: str
) -> None:
    httpx_mock.add_response(
        url=_search_pat(),
        json=_load(fixtures_dir, "itunes_search.json"),
    )
    httpx_mock.add_response(
        url=_reviews_pat("310633997", 1),
        json=_load(fixtures_dir, "itunes_reviews_page1.json"),
    )
    httpx_mock.add_response(
        url=_reviews_pat("310633997", 2),
        json=_load(fixtures_dir, "itunes_reviews_empty.json"),
    )
    httpx_mock.add_response(
        url=_reviews_pat("454638411", 1),
        json=_load(fixtures_dir, "itunes_reviews_empty.json"),
    )

    with AppStoreHKScraper(max_apps_per_search=2) as s:
        posts = list(
            s.search(
                "WhatsApp",
                since=datetime(2020, 1, 1, tzinfo=timezone.utc),
                limit=10,
            )
        )

    assert len(posts) == 5
    p = posts[0]
    assert p.id == "11234567890"
    assert p.source == "app_store_hk"
    assert p.region == "HK"
    assert p.source_category.value == "reviews"
    assert p.signal_type.value == "experience"
    assert p.engagement_metrics["rating"] == 5
    assert p.raw_metadata["app_id"] == "310633997"
    assert p.raw_metadata["version"] == "24.5.1"
    assert p.title == "好用"
    assert p.author_hash and len(p.author_hash) == 64
    # raw author name must not leak into the serialized record
    serialized = p.model_dump_json()
    assert "Mary HK" not in serialized
    assert "John Tsang" not in serialized


def test_numeric_topic_used_as_app_id(
    httpx_mock: HTTPXMock, fixtures_dir: str
) -> None:
    httpx_mock.add_response(
        url=_reviews_pat("310633997", 1),
        json=_load(fixtures_dir, "itunes_reviews_page1.json"),
    )
    httpx_mock.add_response(
        url=_reviews_pat("310633997", 2),
        json=_load(fixtures_dir, "itunes_reviews_empty.json"),
    )

    with AppStoreHKScraper() as s:
        posts = list(
            s.search(
                "310633997",
                since=datetime(2020, 1, 1, tzinfo=timezone.utc),
                limit=10,
            )
        )
    assert len(posts) == 5


def test_since_cutoff_stops_iteration(
    httpx_mock: HTTPXMock, fixtures_dir: str
) -> None:
    httpx_mock.add_response(
        url=_reviews_pat("310633997", 1),
        json=_load(fixtures_dir, "itunes_reviews_page1.json"),
    )
    # Fixture posts dated 2025-03-15, 2025-03-10, 2025-02-20.
    # since=2025-03-12 → only the first qualifies.
    with AppStoreHKScraper() as s:
        posts = list(
            s.search(
                "310633997",
                since=datetime(2025, 3, 12, tzinfo=timezone.utc),
                limit=10,
            )
        )
    assert len(posts) == 1
    assert posts[0].id == "11234567890"


def test_limit_caps_emissions(httpx_mock: HTTPXMock, fixtures_dir: str) -> None:
    httpx_mock.add_response(
        url=_reviews_pat("310633997", 1),
        json=_load(fixtures_dir, "itunes_reviews_page1.json"),
    )
    with AppStoreHKScraper() as s:
        posts = list(
            s.search(
                "310633997",
                since=datetime(2020, 1, 1, tzinfo=timezone.utc),
                limit=2,
            )
        )
    assert len(posts) == 2


def test_no_apps_found_returns_empty(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_search_pat(),
        json={"resultCount": 0, "results": []},
    )
    with AppStoreHKScraper() as s:
        posts = list(
            s.search(
                "asdkjfhasdkjf-unmatched",
                since=datetime(2020, 1, 1, tzinfo=timezone.utc),
                limit=10,
            )
        )
    assert posts == []


def test_500_error_retries_then_raises(httpx_mock: HTTPXMock) -> None:
    # Mark 500 response as reusable so all 4 retry attempts can hit it.
    httpx_mock.add_response(
        url=_search_pat(), status_code=500, is_reusable=True
    )
    with AppStoreHKScraper() as s, pytest.raises(httpx.HTTPStatusError):
        list(
            s.search(
                "WhatsApp",
                since=datetime(2020, 1, 1, tzinfo=timezone.utc),
                limit=10,
            )
        )


def test_404_is_source_error_no_retry(
    httpx_mock: HTTPXMock, fixtures_dir: str
) -> None:
    httpx_mock.add_response(
        url=_search_pat(),
        json=_load(fixtures_dir, "itunes_search.json"),
    )
    # First app: 404 → SourceError; scraper logs and moves to the next app.
    httpx_mock.add_response(
        url=_reviews_pat("310633997", 1),
        status_code=404,
    )
    httpx_mock.add_response(
        url=_reviews_pat("454638411", 1),
        json=_load(fixtures_dir, "itunes_reviews_empty.json"),
    )
    with AppStoreHKScraper(max_apps_per_search=2) as s:
        posts = list(
            s.search(
                "WhatsApp",
                since=datetime(2020, 1, 1, tzinfo=timezone.utc),
                limit=10,
            )
        )
    assert posts == []


def test_fetch_thread_requires_compound_id() -> None:
    with AppStoreHKScraper() as s, pytest.raises(SourceError):
        s.fetch_thread("just-a-review-id")
