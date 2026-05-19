"""Tests for GET /regions — the launcher's source-list backend."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.app import app


def test_regions_endpoint_returns_implemented_regions_only() -> None:
    """A region with zero implemented sources must not appear in the response."""
    c = TestClient(app)
    r = c.get("/regions")
    assert r.status_code == 200
    data = r.json()
    region_ids = {row["region_id"] for row in data}
    # HK, US, TW, JP all have implemented scrapers.
    assert {"HK", "US", "TW", "JP"} <= region_ids
    # Every returned region has at least one source.
    for row in data:
        assert len(row["default_sources"]) + len(row["opt_in_sources"]) > 0


def test_regions_endpoint_separates_default_vs_opt_in() -> None:
    c = TestClient(app)
    data = c.get("/regions").json()
    hk = next(r for r in data if r["region_id"] == "HK")
    # Every default source must have default_enabled=True; every opt-in must
    # have default_enabled=False.
    assert all(s["default_enabled"] is True for s in hk["default_sources"])
    assert all(s["default_enabled"] is False for s in hk["opt_in_sources"])
    # Every opt-in entry must be ToS-prohibited (enforced by the registry validator).
    assert all(
        s["tos_scraping_stance"] == "prohibited" for s in hk["opt_in_sources"]
    )


def test_regions_endpoint_hk_has_known_sources() -> None:
    """Regression: ensure HK's default + opt-in lists include core scrapers."""
    c = TestClient(app)
    data = c.get("/regions").json()
    hk = next(r for r in data if r["region_id"] == "HK")
    default_ids = {s["source_id"] for s in hk["default_sources"]}
    opt_in_ids = {s["source_id"] for s in hk["opt_in_sources"]}

    # Phase 1-9 default-enabled sources for HK:
    assert {"lihkg", "discuss_hk", "reddit_old", "app_store_hk"} <= default_ids
    # Phase 6 opt-in (ToS-prohibited):
    assert {"openrice", "google_play_hk", "medium_hk"} <= opt_in_ids


def test_regions_endpoint_excludes_unimplemented_placeholders() -> None:
    """Registry entries without a scraper class must not appear."""
    c = TestClient(app)
    data = c.get("/regions").json()
    hk = next(r for r in data if r["region_id"] == "HK")
    all_ids = {
        s["source_id"] for s in hk["default_sources"] + hk["opt_in_sources"]
    }
    # baby_kingdom / yahoo_news_hk / threads_hk are placeholder rows with no
    # implementation — they MUST be filtered out.
    placeholders = {
        "baby_kingdom", "yahoo_news_hk", "threads_hk",
        "instagram_public", "xiaohongshu_hk", "hk_lifestyle_blogs",
        "google_serp",
    }
    leaked = all_ids & placeholders
    assert not leaked, f"Placeholder sources leaked into /regions: {leaked}"


def test_regions_endpoint_jp_includes_phase7_wiring() -> None:
    """JP gained quora_jp / medium_jp / youtube_html in Phase 7."""
    c = TestClient(app)
    data = c.get("/regions").json()
    jp = next(r for r in data if r["region_id"] == "JP")
    opt_in_ids = {s["source_id"] for s in jp["opt_in_sources"]}
    assert {"quora_jp", "medium_jp", "youtube_html"} <= opt_in_ids
