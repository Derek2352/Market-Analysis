"""End-to-end persona-card render tests — Playwright-driven."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.render.persona_card import render_persona_card
from src.schemas.synthesis import (
    ClaimList,
    EvidenceClaim,
    Persona,
    RepresentativeQuote,
)


@pytest.fixture
def persona_with_cjk() -> Persona:
    """A persona that exercises CJK glyphs + mixed languages + severity colors."""
    return Persona(
        id="persona_test_cjk_0001",
        run_id="20260519T141500Z",
        cluster_id="c_test",
        name="阿明 — Test Commuter",
        one_liner="HK MTR user — tests CJK and English mix.",
        language="zh-HK",
        demographics={"age_range": "25–45", "region": "HK",
                      "occupation_examples": ["office worker"],
                      "evidence": ["doc_t01"]},
        goals=ClaimList(claims=[EvidenceClaim(claim="Find fare in one tap",
                                              evidence=["doc_t01"])]),
        motivations=ClaimList(claims=[EvidenceClaim(claim="Save commute minutes",
                                                    evidence=["doc_t01"])]),
        pain_points=ClaimList(claims=[
            EvidenceClaim(claim="App lags 嘅 reload silently fails",
                          severity="high", evidence=["doc_t01"]),
            EvidenceClaim(claim="Real-time arrivals wrong at peak",
                          severity="medium", evidence=["doc_t01"]),
        ]),
        preferred_channels=ClaimList(claims=[
            EvidenceClaim(claim="LIHKG threads", evidence=["doc_t01"])]),
        behaviors=ClaimList(claims=[
            EvidenceClaim(claim="Posts about issues on forums",
                          evidence=["doc_t01"])]),
        representative_quotes=[
            RepresentativeQuote(
                text_original="用咗呢個 app 好多年, 個介面好難用",
                text_translated="I've used this app for years, the UI is hard.",
                lang="zh", source="lihkg",
                url="https://lihkg.com/x/1", doc_id="doc_t01",
            ),
            RepresentativeQuote(
                text_original="Octopus reload 喺 MTR app 失敗咗三次, 錢都唔知去咗邊",
                lang="zh", source="app_store_hk",
                url="https://apps.apple.com/hk/x/2", doc_id="doc_t02",
            ),
            RepresentativeQuote(
                text_original="Latest update broke the dark-mode contrast.",
                lang="en", source="reddit_old",
                url="https://old.reddit.com/x/3", doc_id="doc_t03",
            ),
        ],
        data_source_coverage={
            "categories_present": ["forums", "reviews"],
            "categories_missing": ["qa", "blogs", "news_comments",
                                   "social", "video_comments"],
            "sources_used": ["lihkg", "app_store_hk", "reddit_old"],
            "doc_counts": {"lihkg": 4, "app_store_hk": 3, "reddit_old": 1},
            "category_count": 2,
            "coverage_tier": "limited",
            "bias_warning": "Coverage: limited — forums + reviews only.",
        },
        confidence=0.78, cluster_size=27,
        model="claude-sonnet-4-6", provider="anthropic",
    )


@pytest.fixture
def persona_without_quotes(persona_with_cjk: Persona) -> Persona:
    return persona_with_cjk.model_copy(update={"representative_quotes": []})


# ---------------------------------------------------------------------------
# File size, render time, determinism
# ---------------------------------------------------------------------------


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_persona_card_renders_under_size_limit(
    tmp_path: Path, persona_with_cjk: Persona,
) -> None:
    out = render_persona_card(persona_with_cjk, tmp_path / "p.png")
    size_kb = out.stat().st_size / 1024
    assert size_kb <= 400, f"persona card too big: {size_kb:.0f} KB"


def test_persona_card_renders_under_time_ceiling(
    tmp_path: Path, persona_with_cjk: Persona,
) -> None:
    import time
    t0 = time.perf_counter()
    render_persona_card(persona_with_cjk, tmp_path / "p.png")
    dt = time.perf_counter() - t0
    # 10s ceiling per the spec ("avoid flaky CI"). 3s is the target.
    assert dt <= 10.0, f"persona card render too slow: {dt:.2f}s"


def test_persona_card_render_is_deterministic(
    tmp_path: Path, persona_with_cjk: Persona,
) -> None:
    a = render_persona_card(persona_with_cjk, tmp_path / "a.png")
    b = render_persona_card(persona_with_cjk, tmp_path / "b.png")
    assert _sha(a) == _sha(b), "two renders of the same Persona must match byte-for-byte"


# ---------------------------------------------------------------------------
# CJK glyph coverage — no .notdef rectangles
# ---------------------------------------------------------------------------


def test_persona_card_cjk_glyphs_render_with_real_width(
    tmp_path: Path, persona_with_cjk: Persona,
) -> None:
    """Probe several Cantonese-colloquial characters; if Chromium fell back
    to .notdef the measured width would collapse to the floor character."""
    from playwright.sync_api import sync_playwright
    from src.render.core import _chromium_launch_kwargs, get_template
    from src.render.persona_card import _persona_context

    html = get_template("persona_card.html").render(
        **_persona_context(persona_with_cjk)
    )
    page_path = tmp_path / "page.html"
    page_path.write_text(html, encoding="utf-8")

    probes = ["嘅", "咗", "喺", "冇", "用咗呢個"]
    with sync_playwright() as p:
        browser = p.chromium.launch(**_chromium_launch_kwargs())
        try:
            page = browser.new_page()
            page.goto(f"file://{page_path}")
            page.wait_for_load_state("networkidle")
            results = page.evaluate(
                """
                (probes) => {
                  const canvas = document.createElement('canvas');
                  const ctx = canvas.getContext('2d');
                  const cs = getComputedStyle(document.body);
                  ctx.font = `${cs.fontSize} ${cs.fontFamily}`;
                  const floor = ctx.measureText('M').width;
                  return probes.map(s => {
                    const w = ctx.measureText(s).width;
                    return { s, perChar: w / s.length, floor,
                             ok: (w / s.length) > floor * 0.6 };
                  });
                }
                """,
                probes,
            )
        finally:
            browser.close()
    missing = [r["s"] for r in results if not r["ok"]]
    assert not missing, f"CJK glyphs measured as missing: {missing}"


# ---------------------------------------------------------------------------
# Failure-mode placeholders
# ---------------------------------------------------------------------------


def test_persona_card_with_no_quotes_renders_placeholder(
    tmp_path: Path, persona_without_quotes: Persona,
) -> None:
    """A persona with empty representative_quotes should still render — the
    quotes section becomes a placeholder, not a crash."""
    out = render_persona_card(persona_without_quotes, tmp_path / "p.png")
    # File exists and is non-trivial; the precise pixel layout is exercised
    # by the snapshot test, this just guards against an exception.
    assert out.exists()
    assert out.stat().st_size > 10 * 1024  # > 10 KB sanity floor
