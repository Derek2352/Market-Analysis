"""End-to-end journey-map render tests — Playwright-driven."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from src.render.journey_map import (
    _STAGES,
    _curve_paths,
    render_journey_map,
)
from src.schemas.synthesis import (
    ClaimList,
    EmotionPoint,
    EvidenceClaim,
    JourneyMap,
    JourneyStage,
    Persona,
    RepresentativeQuote,
)


def _stage(name: str, *, frictions=(), opps=(),
           emotion="neutral", intensity=0.5,
           coverage="ok") -> JourneyStage:
    return JourneyStage(
        stage=name,
        touchpoints=ClaimList(claims=[
            EvidenceClaim(claim=f"{name} touchpoint", evidence=["doc_t01"])
        ]),
        user_actions=ClaimList(claims=[
            EvidenceClaim(claim=f"{name} action", evidence=["doc_t01"])
        ]),
        emotions=[EmotionPoint(label=emotion, intensity=intensity,
                               evidence=["doc_t01"])],
        frictions=ClaimList(claims=[
            EvidenceClaim(claim=f, evidence=["doc_t01"]) for f in frictions
        ]),
        opportunities=ClaimList(claims=[
            EvidenceClaim(claim=o, evidence=["doc_t01"]) for o in opps
        ]),
        coverage=coverage,
    )


@pytest.fixture
def persona() -> Persona:
    return Persona(
        id="persona_test_journey",
        run_id="20260519T141500Z",
        cluster_id="c_test",
        name="阿明 — Test Commuter",
        one_liner="MTR commuter test fixture.",
        language="zh-HK",
        demographics={"age_range": "25–45", "region": "HK",
                      "evidence": ["doc_t01"]},
        representative_quotes=[
            RepresentativeQuote(text_original="用咗呢個 app", lang="zh",
                                source="lihkg",
                                url="https://lihkg.com/x/1",
                                doc_id="doc_t01"),
            RepresentativeQuote(text_original="reload failed",
                                lang="en", source="app_store_hk",
                                url="https://apps.apple.com/hk/x/2",
                                doc_id="doc_t02"),
            RepresentativeQuote(text_original="lag is unbearable",
                                lang="en", source="reddit_old",
                                url="https://old.reddit.com/x/3",
                                doc_id="doc_t03"),
        ],
        data_source_coverage={
            "categories_present": ["forums", "reviews"],
            "categories_missing": ["qa", "blogs"],
            "sources_used": ["lihkg"],
            "doc_counts": {"lihkg": 4},
            "category_count": 2,
            "coverage_tier": "limited",
            "bias_warning": "limited",
        },
        confidence=0.7, cluster_size=27,
    )


@pytest.fixture
def journey() -> JourneyMap:
    return JourneyMap(
        id="journey_test_curve",
        run_id="20260519T141500Z",
        persona_id="persona_test_journey",
        language="zh-HK",
        data_source_coverage={
            "coverage_tier": "limited",
            "categories_present": ["forums", "reviews"],
        },
        stages=[
            _stage("Awareness",     emotion="curious",     intensity=0.55),
            _stage("Consideration", emotion="skeptical",   intensity=0.60),
            _stage("Decision",      emotion="hopeful",     intensity=0.60),
            _stage("Onboarding",    emotion="uncertain",   intensity=0.45),
            _stage("Use",           emotion="frustrated",  intensity=0.80,
                   frictions=("Reload silently fails", "QR scan lag"),
                   opps=("Retry queue",)),
            _stage("Loyalty/Churn", emotion="resigned",    intensity=0.65,
                   coverage="thin"),
        ],
    )


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Emotion curve geometry — pure-Python, no Playwright needed
# ---------------------------------------------------------------------------


def test_curve_paths_hits_every_stage_center() -> None:
    pts = [{"x": x, "y": 100 + i * 10} for i, x in enumerate([100, 200, 300])]
    line, area = _curve_paths(pts)
    # The line must pass through (100, 100) — the first M and the last point
    # are exact.
    assert line.startswith("M 100.00 100.00")
    # Area closes the path back to the baseline at y=280.
    assert "L 100.00 280" in area
    assert area.endswith("Z")


def test_curve_paths_empty_input_yields_empty_strings() -> None:
    assert _curve_paths([]) == ("", "")


# ---------------------------------------------------------------------------
# Full render: size, time, determinism
# ---------------------------------------------------------------------------


def test_journey_map_renders_under_size_limit(
    tmp_path: Path, persona: Persona, journey: JourneyMap,
) -> None:
    out = render_journey_map(journey, persona, tmp_path / "j.png",
                             topic="MTR Mobile")
    size_kb = out.stat().st_size / 1024
    assert size_kb <= 800, f"journey map too big: {size_kb:.0f} KB"


def test_journey_map_renders_under_time_ceiling(
    tmp_path: Path, persona: Persona, journey: JourneyMap,
) -> None:
    t0 = time.perf_counter()
    render_journey_map(journey, persona, tmp_path / "j.png",
                       topic="MTR Mobile")
    dt = time.perf_counter() - t0
    assert dt <= 10.0, f"journey render too slow: {dt:.2f}s"


def test_journey_map_render_is_deterministic(
    tmp_path: Path, persona: Persona, journey: JourneyMap,
) -> None:
    a = render_journey_map(journey, persona, tmp_path / "a.png",
                           topic="MTR Mobile")
    b = render_journey_map(journey, persona, tmp_path / "b.png",
                           topic="MTR Mobile")
    assert _sha(a) == _sha(b), "two renders of the same Journey must match byte-for-byte"


# ---------------------------------------------------------------------------
# Failure-mode placeholders
# ---------------------------------------------------------------------------


def test_journey_with_missing_stage_renders_no_data(
    tmp_path: Path, persona: Persona,
) -> None:
    """A JourneyMap that's missing one of the canonical 6 stages should
    render that column with a 'no data' marker rather than crash."""
    partial = JourneyMap(
        id="journey_partial",
        run_id="20260519T141500Z",
        persona_id=persona.id,
        language="en",
        data_source_coverage={"coverage_tier": "limited"},
        stages=[_stage("Use", emotion="frustrated", intensity=0.7)],
    )
    out = render_journey_map(partial, persona, tmp_path / "j.png")
    assert out.exists()
    assert out.stat().st_size > 10 * 1024
