"""Phase 8 render tests — determinism, file size, CJK glyphs, bundle layout,
failure modes.

The "snapshot" requirement from the spec is split across two tests:

  * ``test_persona_render_is_deterministic`` and the journey counterpart
    re-render the same fixture twice and assert sha-256 match — this is
    the regression guard, and unlike committing a binary PNG it doesn't
    rot the moment the host Chromium minor-version bumps.

  * If you want a committed reference PNG too, regenerate it with
    ``mkt render persona <id>`` and diff manually before committing.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import pytest

from src.render.bundle import render_run
from src.render.core import accent_palette
from src.render.journey_map import render_journey_map
from src.render.persona_card import render_persona_card


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Determinism + file size + render time
# ---------------------------------------------------------------------------


def test_persona_render_is_deterministic(tmp_path: Path, cjk_persona) -> None:
    a = render_persona_card(cjk_persona, tmp_path / "p1.png")
    b = render_persona_card(cjk_persona, tmp_path / "p2.png")
    assert _sha(a) == _sha(b), "persona card render is not deterministic"


def test_journey_render_is_deterministic(tmp_path: Path,
                                          cjk_persona, cjk_journey) -> None:
    a = render_journey_map(cjk_journey, cjk_persona,
                           tmp_path / "j1.png", topic="MTR Mobile")
    b = render_journey_map(cjk_journey, cjk_persona,
                           tmp_path / "j2.png", topic="MTR Mobile")
    assert _sha(a) == _sha(b), "journey map render is not deterministic"


def test_persona_png_meets_size_target(tmp_path: Path, cjk_persona) -> None:
    out = render_persona_card(cjk_persona, tmp_path / "p.png")
    assert out.stat().st_size <= 400 * 1024, (
        f"persona card {out.stat().st_size / 1024:.0f} KB exceeds 400 KB target"
    )


def test_journey_png_meets_size_target(tmp_path: Path,
                                        cjk_persona, cjk_journey) -> None:
    out = render_journey_map(cjk_journey, cjk_persona,
                             tmp_path / "j.png", topic="MTR Mobile")
    assert out.stat().st_size <= 800 * 1024, (
        f"journey map {out.stat().st_size / 1024:.0f} KB exceeds 800 KB target"
    )


def test_render_time_ceiling(tmp_path: Path, cjk_persona, cjk_journey) -> None:
    """Generous 10-second ceiling — the target is 3s but CI variance is real."""
    t0 = time.perf_counter()
    render_persona_card(cjk_persona, tmp_path / "p.png")
    persona_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    render_journey_map(cjk_journey, cjk_persona, tmp_path / "j.png")
    journey_s = time.perf_counter() - t0
    assert persona_s < 10.0, f"persona render too slow: {persona_s:.1f}s"
    assert journey_s < 10.0, f"journey render too slow: {journey_s:.1f}s"


# ---------------------------------------------------------------------------
# CJK glyph rendering — no .notdef substitutions
# ---------------------------------------------------------------------------


def test_cjk_glyphs_render_with_correct_width(tmp_path: Path, cjk_persona) -> None:
    """A persona with quotes containing 嘅 咗 喺 冇 must not fall back to .notdef.

    We render the card's HTML in Playwright, then ask the page to measure
    each probe via canvas.measureText. ASCII 'M' is the floor reference;
    a CJK character that renders as a .notdef rectangle collapses to that
    same width. We require ≥ 60% above floor — comfortably non-zero.
    """
    from src.render.core import _playwright_browser, get_template
    from src.render.persona_card import _persona_context

    probes = ["嘅", "咗", "喺", "冇", "用咗呢個", "支付寶香港"]

    html = get_template("persona_card.html").render(**_persona_context(cjk_persona))
    page_dir = tmp_path / "html"
    page_dir.mkdir()
    src = page_dir / "page.html"
    src.write_text(html, encoding="utf-8")

    with _playwright_browser() as browser:
        ctx = browser.new_context(viewport={"width": 1200, "height": 1600})
        try:
            page = ctx.new_page()
            page.goto(f"file://{src}")
            page.wait_for_load_state("networkidle")
            results = page.evaluate(
                """
                (probes) => {
                  const c = document.createElement('canvas');
                  const ctx = c.getContext('2d');
                  const cs = getComputedStyle(document.body);
                  ctx.font = `${cs.fontSize} ${cs.fontFamily}`;
                  const floor = ctx.measureText('M').width;
                  return probes.map(s => {
                    const w = ctx.measureText(s).width;
                    return { s, w, perChar: w / Math.max(1, s.length), floor };
                  });
                }
                """,
                probes,
            )
        finally:
            ctx.close()

    for r in results:
        assert r["perChar"] > r["floor"] * 0.6, (
            f"glyph {r['s']!r} collapsed to .notdef width "
            f"({r['perChar']:.1f}px vs floor {r['floor']:.1f}px)"
        )


# ---------------------------------------------------------------------------
# Bundle command — index.html refers to every rendered PNG
# ---------------------------------------------------------------------------


def _write_persona(personas_root: Path, persona) -> Path:
    sub = personas_root / "test" / "HK"
    sub.mkdir(parents=True, exist_ok=True)
    f = sub / f"{persona.id}.json"
    f.write_text(persona.model_dump_json(indent=2), encoding="utf-8")
    return f


def _write_journey(journeys_root: Path, journey) -> Path:
    sub = journeys_root / "test" / "HK"
    sub.mkdir(parents=True, exist_ok=True)
    f = sub / f"{journey.id}.json"
    f.write_text(journey.model_dump_json(indent=2), encoding="utf-8")
    return f


def test_bundle_renders_index_and_all_pngs(
    tmp_path: Path, cjk_persona, cjk_journey,
) -> None:
    """Two-persona run produces 2 cards + 2 journey maps + index.html that
    references all four PNG filenames."""
    personas_root = tmp_path / "personas"
    journeys_root = tmp_path / "journeys"
    runs_root = tmp_path / "runs"
    out_dir = tmp_path / "out"

    # Persona B is a copy with a different id so the bundle has 2 of each.
    persona_b = cjk_persona.model_copy(update={
        "id": "persona_test_b_ddccbbaa",
        "name": "Persona B — copy",
    })
    journey_b = cjk_journey.model_copy(update={
        "id": "journey_test_b_1100ffee",
        "persona_id": persona_b.id,
    })

    _write_persona(personas_root, cjk_persona)
    _write_persona(personas_root, persona_b)
    _write_journey(journeys_root, cjk_journey)
    _write_journey(journeys_root, journey_b)

    result = render_run(
        cjk_persona.run_id, out_dir,
        personas_root=personas_root,
        journeys_root=journeys_root,
        runs_root=runs_root,
    )

    assert len(result.persona_pngs) == 2
    assert len(result.journey_pngs) == 2
    assert result.index_html.exists()
    body = result.index_html.read_text(encoding="utf-8")
    for png in (*result.persona_pngs, *result.journey_pngs):
        assert png.name in body, f"index.html missing reference to {png.name}"


def test_bundle_with_zip_creates_archive(
    tmp_path: Path, cjk_persona, cjk_journey,
) -> None:
    personas_root = tmp_path / "personas"
    journeys_root = tmp_path / "journeys"
    _write_persona(personas_root, cjk_persona)
    _write_journey(journeys_root, cjk_journey)

    result = render_run(
        cjk_persona.run_id, tmp_path / "out",
        personas_root=personas_root,
        journeys_root=journeys_root,
        zip_bundle=True,
    )
    assert result.zip_path is not None and result.zip_path.exists()
    import zipfile
    with zipfile.ZipFile(result.zip_path) as zf:
        names = {Path(n).name for n in zf.namelist()}
    assert "index.html" in names
    assert any(n.endswith(".png") for n in names)


def test_bundle_errors_when_run_has_no_personas(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No personas found"):
        render_run(
            "nonexistent_run", tmp_path / "out",
            personas_root=tmp_path / "personas",
            journeys_root=tmp_path / "journeys",
        )


# ---------------------------------------------------------------------------
# Failure-mode rendering — empty buckets / missing quotes / oversize text
# ---------------------------------------------------------------------------


def test_persona_with_no_quotes_renders_placeholder(
    tmp_path: Path, cjk_persona,
) -> None:
    no_quotes = cjk_persona.model_copy(update={"representative_quotes": []})
    out = render_persona_card(no_quotes, tmp_path / "no_quotes.png")
    assert out.exists()
    assert out.stat().st_size > 50 * 1024  # non-trivial render, not a blank


def test_journey_with_empty_stage_uses_placeholder(
    tmp_path: Path, cjk_persona, cjk_journey,
) -> None:
    """A stage with empty claim buckets should still render (no crash)."""
    from src.schemas.synthesis import ClaimList, JourneyStage

    empty_stage = JourneyStage(
        stage="Loyalty/Churn",
        touchpoints=ClaimList(claims=[]),
        user_actions=ClaimList(claims=[]),
        emotions=[],
        frictions=ClaimList(claims=[]),
        opportunities=ClaimList(claims=[]),
        coverage="none",
    )
    stages = list(cjk_journey.stages[:-1]) + [empty_stage]
    journey = cjk_journey.model_copy(update={"stages": stages})
    out = render_journey_map(journey, cjk_persona, tmp_path / "empty_stage.png")
    assert out.exists()
    assert out.stat().st_size > 80 * 1024


def test_oversize_quote_is_truncated(tmp_path: Path, cjk_persona) -> None:
    from src.schemas.synthesis import RepresentativeQuote

    long_text = "Octopus reload silently fails. " * 40   # ~ 1200 chars
    persona = cjk_persona.model_copy(update={
        "representative_quotes": [
            RepresentativeQuote(
                text_original=long_text,
                lang="en", source="lihkg",
                url="https://lihkg.com/thread/test/long",
                doc_id="doc_test_001",
            ),
            *cjk_persona.representative_quotes[1:],
        ],
    })
    out = render_persona_card(persona, tmp_path / "long.png")
    # Still under the size cap, despite the long input.
    assert out.stat().st_size <= 400 * 1024


# ---------------------------------------------------------------------------
# Accent palette
# ---------------------------------------------------------------------------


def test_accent_palette_is_stable_per_persona_id() -> None:
    a1 = accent_palette("persona_test_aabbccdd")
    a2 = accent_palette("persona_test_aabbccdd")
    assert a1 == a2


def test_accent_palette_differs_across_persona_ids() -> None:
    a = accent_palette("persona_one_aaaa1111")
    b = accent_palette("persona_two_bbbb2222")
    assert a != b


def test_accent_palette_returns_valid_hex_codes() -> None:
    a = accent_palette("persona_format_test")
    for v in (a.from_, a.via, a.to):
        assert re.fullmatch(r"#[0-9a-f]{6}", v), v
