"""Phase 6 — coverage_tier + category_count in data_source_coverage."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.pipeline.synthesize import _build_coverage, _coverage_tier
from src.schemas.cluster import Cluster


def _cluster(sources: dict[str, int]) -> Cluster:
    post_ids = [f"p{i}" for i in range(sum(sources.values()))]
    return Cluster(
        cluster_id="c",
        topic="t",
        region="HK",
        size=len(post_ids),
        post_ids=post_ids,
        representative_post_ids=post_ids[:3],
        keyword_summary=["k"],
        source_distribution=sources,
        language_distribution={"zh": len(post_ids)},
        generated_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, "single-perspective"),
        (1, "single-perspective"),
        (2, "limited"),
        (3, "balanced"),
        (4, "balanced"),
        (5, "high"),
        (7, "high"),
    ],
)
def test_coverage_tier_mapping(count: int, expected: str) -> None:
    assert _coverage_tier(count) == expected


def test_single_perspective_one_category() -> None:
    cov = _build_coverage(_cluster({"lihkg": 10}), region="HK")
    assert cov["category_count"] == 1
    assert cov["coverage_tier"] == "single-perspective"
    assert cov["categories_present"] == ["forums"]


def test_limited_two_categories() -> None:
    # lihkg (forums) + app_store_hk (reviews) → 2 categories
    cov = _build_coverage(
        _cluster({"lihkg": 5, "app_store_hk": 5}),
        region="HK",
    )
    assert cov["category_count"] == 2
    assert cov["coverage_tier"] == "limited"
    assert "forums" in cov["categories_present"]
    assert "reviews" in cov["categories_present"]


def test_balanced_three_to_four_categories() -> None:
    # lihkg(forums) + app_store_hk(reviews) + hk01(news_comments) +
    # quora_hk(qa) → 4 categories
    cov = _build_coverage(
        _cluster({"lihkg": 4, "app_store_hk": 4, "hk01": 4, "quora_hk": 4}),
        region="HK",
    )
    assert cov["category_count"] == 4
    assert cov["coverage_tier"] == "balanced"


def test_high_five_or_more_categories() -> None:
    cov = _build_coverage(
        _cluster({
            "lihkg": 3, "app_store_hk": 3, "hk01": 3,
            "quora_hk": 3, "medium_hk": 3,
        }),
        region="HK",
    )
    assert cov["category_count"] == 5
    assert cov["coverage_tier"] == "high"


def test_reddit_old_now_contributes_to_forums_not_qa() -> None:
    """After the Phase 6 move, a cluster of all reddit_old posts has
    categories_present=['forums'], not ['qa']."""
    cov = _build_coverage(_cluster({"reddit_old": 10}), region="HK")
    assert cov["categories_present"] == ["forums"]
    assert "qa" in cov["categories_missing"]


def test_robots_disallowed_source_is_flagged_in_the_coverage_note() -> None:
    """Evidence from a source whose robots.txt disallows the endpoint it was
    collected from (registry robots_txt_allows=False) carries a provenance
    warning, and it leads the bias_warning that the card footer shows."""
    cov = _build_coverage(
        _cluster({"app_store_hk": 8, "lihkg": 2}), region="HK",
    )
    expected = "app_store_hk data collected from a robots.txt-disallowed feed"
    assert cov["provenance_warning"] == expected
    assert cov["bias_warning"].startswith(expected + "; ")
    # The ordinary coverage note is kept after it.
    assert "80% of evidence from a single source (app_store_hk)" in cov["bias_warning"]


def test_provenance_warning_survives_an_unknown_region_code() -> None:
    cov = _build_coverage(_cluster({"app_store_hk": 3}), region="XX")
    assert cov["provenance_warning"].startswith("app_store_hk data collected")


def test_robots_allowed_sources_carry_no_provenance_warning() -> None:
    cov = _build_coverage(_cluster({"lihkg": 5, "openrice": 5}), region="HK")
    assert "provenance_warning" not in cov
    assert "robots.txt" not in cov["bias_warning"]
