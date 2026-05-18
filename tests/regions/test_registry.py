"""Registry-shape tests for Phase 6.

Catches accidental drift in the HK registry: opt-in gating, the qa->forums
reddit_old move, default_source_ids excluding prohibited sources, and the
validator that prevents a prohibited source from being default-enabled.
"""
from __future__ import annotations

import pytest

from src.regions.registry import (
    AccessMethod,
    REGIONS,
    SourceConfig,
    TosRisk,
    get_region,
)
from src.schemas.enums import SignalType, SourceCategory, ToSStance


def test_validator_blocks_prohibited_default_enabled() -> None:
    """A prohibited source must declare default_enabled=False or fail import."""
    with pytest.raises(Exception):  # ValidationError wraps the ValueError
        SourceConfig(
            source_id="bad",
            category=SourceCategory.FORUMS,
            priority=1,
            access_method=AccessMethod.HTML,
            tos_risk=TosRisk.HIGH,
            auth_required=False,
            signal_type=SignalType.OPINION,
            persona_value=3,
            journey_value=3,
            default_enabled=True,                      # offending pair
            tos_scraping_stance=ToSStance.PROHIBITED,
        )


def test_validator_accepts_prohibited_when_opt_in() -> None:
    sc = SourceConfig(
        source_id="ok_opt_in",
        category=SourceCategory.FORUMS,
        priority=1,
        access_method=AccessMethod.HTML,
        tos_risk=TosRisk.HIGH,
        auth_required=False,
        signal_type=SignalType.OPINION,
        persona_value=3,
        journey_value=3,
        default_enabled=False,
        tos_scraping_stance=ToSStance.PROHIBITED,
    )
    assert sc.default_enabled is False


def test_hk_phase6_categories_populated() -> None:
    """HK should have at least 5 of 7 source categories represented across
    default + opt-in entries after Phase 6."""
    hk = get_region("HK")
    all_sources = [s for s in hk.sources if not s.excluded_by_constraint]
    categories = {s.category.value for s in all_sources}
    # Discuss + reddit_old (forums), App Store HK + Openrice + Google Play
    # (reviews), HK01 (news), YouTube_html (video), Quora_HK (qa), Medium
    # (blogs). 6 of 7 (social still empty).
    assert {
        "forums", "reviews", "news_comments",
        "video_comments", "qa", "blogs",
    } <= categories


def test_hk_default_source_ids_excludes_prohibited() -> None:
    """Default source list never includes a default_enabled=False source."""
    hk = get_region("HK")
    defaults = set(hk.default_source_ids())
    for s in hk.opt_in_sources():
        assert s.source_id not in defaults, (
            f"{s.source_id!r} is opt-in but appears in default_source_ids"
        )
    must_be_default = {"lihkg", "discuss_hk", "reddit_old"}
    assert must_be_default <= defaults


def test_reddit_old_is_now_a_forums_source() -> None:
    hk = get_region("HK")
    reddit = hk.get_source("reddit_old")
    assert reddit is not None
    assert reddit.category == SourceCategory.FORUMS
    # Move shouldn't change the signal type — still recommendation-heavy.
    assert reddit.signal_type == SignalType.RECOMMENDATION


def test_phase6_opt_in_entries_match_spec() -> None:
    """All four Phase 6 prohibited entries are present, opt-in, prohibited.

    Of the four originally planned: medium_hk shipped (Path C), the other
    three deferred to a Phase 6.5 follow-up because their fixtures didn't
    support implementation. Deferred entries keep their stance/category
    but have last_verified_working=None until they're built.
    """
    hk = get_region("HK")
    expected = {
        "hk01": (SourceCategory.NEWS_COMMENTS, None),         # deferred
        "youtube_html": (SourceCategory.VIDEO_COMMENTS, None), # deferred
        "quora_hk": (SourceCategory.QA, None),                # deferred
        "medium_hk": (SourceCategory.BLOGS, "implemented"),   # shipped
    }
    for sid, (cat, status) in expected.items():
        sc = hk.get_source(sid)
        assert sc is not None, f"{sid} missing from HK"
        assert sc.category == cat, f"{sid} miscategorized as {sc.category}"
        assert sc.tos_scraping_stance == ToSStance.PROHIBITED, (
            f"{sid} should be ToS-prohibited"
        )
        assert sc.default_enabled is False, f"{sid} should be opt-in"
        if status == "implemented":
            assert sc.last_verified_working is not None, (
                f"{sid} is implemented; last_verified_working should be set"
            )
        else:
            assert sc.last_verified_working is None, (
                f"{sid} is deferred; last_verified_working should be None until built"
            )


def test_discuss_hk_is_default_enabled_silent_stance() -> None:
    hk = get_region("HK")
    sc = hk.get_source("discuss_hk")
    assert sc is not None
    assert sc.category == SourceCategory.FORUMS
    assert sc.tos_scraping_stance == ToSStance.SILENT
    assert sc.default_enabled is True
