"""Bundle command tests — exercise the full render-run path on a 2-persona run."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from src.render.bundle import render_run
from src.schemas.synthesis import (
    ClaimList,
    EmotionPoint,
    EvidenceClaim,
    JourneyMap,
    JourneyStage,
    Persona,
    RepresentativeQuote,
)


RUN_ID = "20260519T999999Z"


def _make_persona(pid: str, name: str) -> Persona:
    return Persona(
        id=pid, run_id=RUN_ID, cluster_id=f"c_{pid}",
        name=name, one_liner=f"Test fixture {pid}.",
        language="en",
        demographics={"age_range": "25–45", "region": "HK",
                      "evidence": ["doc_x01"]},
        goals=ClaimList(claims=[
            EvidenceClaim(claim="g", evidence=["doc_x01"])]),
        motivations=ClaimList(claims=[
            EvidenceClaim(claim="m", evidence=["doc_x01"])]),
        pain_points=ClaimList(claims=[
            EvidenceClaim(claim="p", severity="high", evidence=["doc_x01"])]),
        preferred_channels=ClaimList(claims=[
            EvidenceClaim(claim="c", evidence=["doc_x01"])]),
        behaviors=ClaimList(claims=[
            EvidenceClaim(claim="b", evidence=["doc_x01"])]),
        representative_quotes=[
            RepresentativeQuote(text_original="quote 1", lang="en",
                                source="lihkg",
                                url="https://lihkg.com/x", doc_id="doc_x01"),
            RepresentativeQuote(text_original="quote 2", lang="en",
                                source="lihkg",
                                url="https://lihkg.com/y", doc_id="doc_x02"),
            RepresentativeQuote(text_original="quote 3", lang="en",
                                source="lihkg",
                                url="https://lihkg.com/z", doc_id="doc_x03"),
        ],
        data_source_coverage={
            "categories_present": ["forums"],
            "categories_missing": ["qa"],
            "sources_used": ["lihkg"],
            "doc_counts": {"lihkg": 3},
            "category_count": 1,
            "coverage_tier": "single-perspective",
            "bias_warning": "Single source category.",
        },
        confidence=0.5, cluster_size=10,
    )


def _make_journey(persona_id: str, jid: str) -> JourneyMap:
    stage = JourneyStage(
        stage="Use",
        touchpoints=ClaimList(claims=[
            EvidenceClaim(claim="t", evidence=["doc_x01"])]),
        user_actions=ClaimList(claims=[
            EvidenceClaim(claim="a", evidence=["doc_x01"])]),
        emotions=[EmotionPoint(label="frustrated", intensity=0.7,
                               evidence=["doc_x01"])],
        frictions=ClaimList(claims=[
            EvidenceClaim(claim="f", evidence=["doc_x01"])]),
        opportunities=ClaimList(claims=[
            EvidenceClaim(claim="o", evidence=["doc_x01"])]),
    )
    return JourneyMap(
        id=jid, run_id=RUN_ID, persona_id=persona_id, language="en",
        data_source_coverage={"coverage_tier": "single-perspective"},
        stages=[stage],
    )


@pytest.fixture
def run_data_tree(tmp_path: Path) -> dict[str, Path]:
    """Lay down a two-persona, two-journey run under tmp_path."""
    personas_root = tmp_path / "personas" / "topic" / "HK"
    journeys_root = tmp_path / "journeys" / "topic" / "HK"
    runs_root = tmp_path / "runs"
    personas_root.mkdir(parents=True)
    journeys_root.mkdir(parents=True)
    (runs_root / RUN_ID).mkdir(parents=True)

    pA = _make_persona("persona_test_aa00", "Alice")
    pB = _make_persona("persona_test_bb11", "Bob")
    jA = _make_journey(pA.id, "journey_test_aa00")
    jB = _make_journey(pB.id, "journey_test_bb11")
    (personas_root / f"{pA.id}.json").write_text(
        pA.model_dump_json(indent=2), encoding="utf-8")
    (personas_root / f"{pB.id}.json").write_text(
        pB.model_dump_json(indent=2), encoding="utf-8")
    (journeys_root / f"{jA.id}.json").write_text(
        jA.model_dump_json(indent=2), encoding="utf-8")
    (journeys_root / f"{jB.id}.json").write_text(
        jB.model_dump_json(indent=2), encoding="utf-8")
    (runs_root / RUN_ID / "run.json").write_text(json.dumps({
        "summary": {"run_id": RUN_ID, "topic": "Test Topic", "region": "HK",
                    "sources": [], "status": "succeeded",
                    "created_at": "2026-05-19T00:00:00+00:00",
                    "finished_at": "2026-05-19T00:00:00+00:00",
                    "error": None,
                    "counts": {"posts": 0, "clusters": 2,
                               "personas": 2, "journeys": 2}},
        "params": {},
    }), encoding="utf-8")
    return {
        "personas_root": tmp_path / "personas",
        "journeys_root": tmp_path / "journeys",
        "runs_root": runs_root,
    }


def test_render_run_emits_persona_and_journey_pngs(
    tmp_path: Path, run_data_tree: dict[str, Path],
) -> None:
    out = tmp_path / "bundle"
    result = render_run(
        RUN_ID,
        out,
        personas_root=run_data_tree["personas_root"],
        journeys_root=run_data_tree["journeys_root"],
        runs_root=run_data_tree["runs_root"],
        zip_bundle=True,
    )
    assert len(result.persona_pngs) == 2
    assert len(result.journey_pngs) == 2
    for p in (*result.persona_pngs, *result.journey_pngs):
        assert p.exists() and p.stat().st_size > 10 * 1024


def test_render_run_index_html_references_every_png(
    tmp_path: Path, run_data_tree: dict[str, Path],
) -> None:
    out = tmp_path / "bundle"
    result = render_run(
        RUN_ID, out,
        personas_root=run_data_tree["personas_root"],
        journeys_root=run_data_tree["journeys_root"],
        runs_root=run_data_tree["runs_root"],
    )
    html = result.index_html.read_text(encoding="utf-8")
    for p in (*result.persona_pngs, *result.journey_pngs):
        assert p.name in html, f"index.html missing reference to {p.name}"


def test_render_run_zip_contains_all_artifacts(
    tmp_path: Path, run_data_tree: dict[str, Path],
) -> None:
    out = tmp_path / "bundle"
    result = render_run(
        RUN_ID, out,
        personas_root=run_data_tree["personas_root"],
        journeys_root=run_data_tree["journeys_root"],
        runs_root=run_data_tree["runs_root"],
        zip_bundle=True,
    )
    assert result.zip_path is not None and result.zip_path.exists()
    with zipfile.ZipFile(result.zip_path) as zf:
        names = zf.namelist()
    # 2 persona pngs + 2 journey pngs + index.html
    assert sum(n.endswith(".png") for n in names) == 4
    assert any(n.endswith("index.html") for n in names)


def test_render_run_clear_error_when_run_id_unknown(
    tmp_path: Path, run_data_tree: dict[str, Path],
) -> None:
    with pytest.raises(FileNotFoundError, match="No personas found"):
        render_run(
            "20260519T000000Z-doesnotexist",
            tmp_path / "bundle",
            personas_root=run_data_tree["personas_root"],
            journeys_root=run_data_tree["journeys_root"],
            runs_root=run_data_tree["runs_root"],
        )
