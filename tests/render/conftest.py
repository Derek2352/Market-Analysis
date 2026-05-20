"""Shared fixtures + skip-guards for the Phase 8 render tests.

These tests drive Playwright against a real Chromium build, which isn't
installed by default. Tests skip cleanly when the binary isn't present —
they're the kind of thing you run locally after `playwright install
chromium`, not on every CI tick.

We also build a small in-memory Persona + JourneyMap pair that carries
Cantonese-colloquial characters so a single fixture exercises both the
general renderer plumbing and the CJK glyph requirement from the spec.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.schemas.synthesis import (
    ClaimList,
    EmotionPoint,
    EvidenceClaim,
    JourneyMap,
    JourneyStage,
    Persona,
    RepresentativeQuote,
)


# ---------------------------------------------------------------------------
# Playwright + Chromium availability gate
# ---------------------------------------------------------------------------


def _chromium_available() -> bool:
    env_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env_path and os.path.exists(env_path):
        return True
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    # Best-effort check: ask Playwright to find chromium without launching.
    # An exception here means the browsers aren't installed.
    try:
        from playwright._impl._driver import compute_driver_executable  # noqa: F401
    except Exception:
        return False
    # Check both Linux/macOS and Windows cache locations
    for cache in [
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.expanduser("~/AppData/Local/ms-playwright"),
    ]:
        if os.path.isdir(cache):
            for entry in os.listdir(cache):
                if entry.startswith("chromium"):
                    return True
    return False


@pytest.fixture(scope="session", autouse=True)
def _skip_if_no_chromium() -> None:
    if not _chromium_available():
        pytest.skip(
            "Chromium for Playwright not available. Run "
            "`playwright install chromium` or set "
            "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH.",
            allow_module_level=True,
        )


def pytest_collection_modifyitems(config, items) -> None:
    """Tag every render test ``slow`` so callers can drop them with -m 'not slow'."""
    slow = pytest.mark.slow
    for item in items:
        item.add_marker(slow)


# ---------------------------------------------------------------------------
# Synthetic Persona + JourneyMap (single fixture exercises CJK too)
# ---------------------------------------------------------------------------


def _claims(*claims: tuple[str, str | None, list[str]]) -> ClaimList:
    return ClaimList(claims=[
        EvidenceClaim(claim=c, severity=sev, evidence=ev)
        for c, sev, ev in claims
    ])


@pytest.fixture(scope="session")
def cjk_persona() -> Persona:
    """HK persona with Cantonese-colloquial quotes (嘅 咗 喺 冇)."""
    quotes = [
        RepresentativeQuote(
            text_original="用咗呢個 app 好多年, 介面真係好難用",
            text_translated="I've used this app for years; the interface is hard to use.",
            lang="zh",
            source="lihkg",
            url="https://lihkg.com/thread/test/post/1",
            doc_id="doc_test_001",
        ),
        RepresentativeQuote(
            text_original="個 Octopus reload 喺呢度成日失敗, 冇人理",
            text_translated="Octopus reload fails here all the time, no one cares.",
            lang="zh",
            source="app_store_hk",
            url="https://apps.apple.com/hk/app/test#review-2",
            doc_id="doc_test_002",
        ),
        RepresentativeQuote(
            text_original="Latest update is slower than before — three taps to check a fare.",
            lang="en",
            source="reddit_old",
            url="https://old.reddit.com/r/HongKong/comments/test/post3",
            doc_id="doc_test_003",
        ),
    ]
    return Persona(
        id="persona_test_aabbccdd",
        run_id="20260519T000000Z",
        cluster_id="cluster_test",
        name="阿明 — Test Persona",
        one_liner="HK rider who relies on the app for daily commute, 嘅 咗 喺 冇 verbatim.",
        language="zh-HK",
        demographics={
            "age_range": "25–45",
            "occupation_examples": ["office worker"],
            "region": "HK",
            "evidence": ["doc_test_001"],
        },
        goals=_claims(("Look up fare in one tap", None, ["doc_test_001"])),
        motivations=_claims(("Save time at gates", None, ["doc_test_001"])),
        pain_points=_claims(
            ("Octopus reload silently fails",  "high",   ["doc_test_002"]),
            ("Interface lag on common screens", "medium", ["doc_test_003"]),
        ),
        preferred_channels=_claims(("LIHKG", None, ["doc_test_001"])),
        behaviors=_claims(("Force-closes when it lags", None, ["doc_test_003"])),
        representative_quotes=quotes,
        data_source_coverage={
            "categories_present": ["forums", "reviews"],
            "categories_missing": ["qa", "blogs", "news_comments",
                                   "social", "video_comments"],
            "sources_used": ["lihkg", "reddit_old", "app_store_hk"],
            "doc_counts": {"lihkg": 1, "reddit_old": 1, "app_store_hk": 1},
            "bias_warning": "Coverage: limited — only forums + reviews represented.",
            "category_count": 2,
            "coverage_tier": "limited",
        },
        confidence=0.74,
        cluster_size=12,
        generated_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        model="claude-sonnet-4-6",
        provider="anthropic",
    )


def _stage(name: str, *, emotion: str, intensity: float,
           coverage: str = "ok") -> JourneyStage:
    return JourneyStage(
        stage=name,
        touchpoints=_claims((f"{name} touchpoint", None, ["doc_test_001"])),
        user_actions=_claims((f"{name} action", None, ["doc_test_001"])),
        emotions=[EmotionPoint(label=emotion, intensity=intensity,
                               evidence=["doc_test_001"])],
        frictions=_claims((f"{name} friction", None, ["doc_test_002"])),
        opportunities=_claims((f"{name} opportunity", None, ["doc_test_003"])),
        coverage=coverage,
    )


@pytest.fixture(scope="session")
def cjk_journey(cjk_persona: Persona) -> JourneyMap:
    return JourneyMap(
        id="journey_test_eeff0011",
        run_id=cjk_persona.run_id,
        persona_id=cjk_persona.id,
        language="zh-HK",
        data_source_coverage=cjk_persona.data_source_coverage,
        stages=[
            _stage("Awareness",     emotion="curious",    intensity=0.55),
            _stage("Consideration", emotion="skeptical",  intensity=0.55),
            _stage("Decision",      emotion="hopeful",    intensity=0.60),
            _stage("Onboarding",    emotion="uncertain",  intensity=0.45),
            _stage("Use",           emotion="frustrated", intensity=0.78),
            _stage("Loyalty/Churn", emotion="resigned",   intensity=0.65,
                   coverage="thin"),
        ],
        generated_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        model="claude-sonnet-4-6",
        provider="anthropic",
    )
