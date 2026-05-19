"""Tests for the eval suite — fixture loading, scoring, full-suite run.

All tests use ``provider="mock"`` so no API key / network is needed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.eval.runner import (
    EVAL_DIR,
    _resolve_placeholders,
    _theme_recovered,
    list_fixtures,
    load_fixture,
    run_eval,
    run_eval_suite,
)
from src.pipeline.synthesize import _doc_id_for


@pytest.fixture(autouse=True)
def _hash_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HASH_SALT", "test-eval-salt")


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------


def test_list_fixtures_finds_all_five_products() -> None:
    paths = list_fixtures()
    names = {p.stem for p in paths}
    assert names >= {
        "whatsapp_hk", "mtr_mobile_hk", "tabelog_jp",
        "iphone_us", "dcard_tw",
    }


def test_each_fixture_loads_and_has_required_keys() -> None:
    required = {
        "name", "topic", "region", "posts", "clusters",
        "expected_pain_points",
        "mock_persona_responses", "mock_journey_responses",
    }
    for path in list_fixtures():
        data = load_fixture(path)
        assert required <= set(data.keys()), (
            f"{path.name} missing: {required - set(data.keys())}"
        )
        # Every cluster must have a paired persona + journey mock.
        cluster_ids = {c["cluster_id"] for c in data["clusters"]}
        assert cluster_ids == set(data["mock_persona_responses"].keys())
        assert cluster_ids == set(data["mock_journey_responses"].keys())


# ---------------------------------------------------------------------------
# Placeholder resolution
# ---------------------------------------------------------------------------


def test_resolve_placeholders_swaps_post_tokens_for_doc_ids() -> None:
    node = {
        "claim": "x",
        "evidence": ["<post_001>", "<post_002>"],
        "nested": {"doc_id": "<post_003>"},
    }
    resolved = _resolve_placeholders(node)
    assert resolved["evidence"] == [_doc_id_for("post_001"), _doc_id_for("post_002")]
    assert resolved["nested"]["doc_id"] == _doc_id_for("post_003")


def test_resolve_placeholders_leaves_non_matching_strings() -> None:
    assert _resolve_placeholders("plain text") == "plain text"
    assert _resolve_placeholders("doc_already_hashed") == "doc_already_hashed"


# ---------------------------------------------------------------------------
# Theme recovery
# ---------------------------------------------------------------------------


def test_theme_recovered_matches_via_keywords() -> None:
    theme = {"theme": "battery", "keywords": ["battery", "drain"], "phrases": []}
    assert _theme_recovered(theme, ["My phone has terrible battery life"])
    assert _theme_recovered(theme, ["Drain is unbearable after the update"])
    assert not _theme_recovered(theme, ["Screen quality is fine"])


def test_theme_recovered_matches_via_phrase() -> None:
    theme = {"theme": "no record", "keywords": [], "phrases": ["no record"]}
    assert _theme_recovered(theme, ["Restaurant had no record of booking"])
    assert not _theme_recovered(theme, ["Booking confirmed"])


def test_theme_recovered_keyword_token_is_case_insensitive() -> None:
    theme = {"theme": "spam", "keywords": ["Spam"], "phrases": []}
    assert _theme_recovered(theme, ["SPAM messages keep arriving"])


# ---------------------------------------------------------------------------
# Single-fixture run
# ---------------------------------------------------------------------------


def test_run_eval_on_whatsapp_fixture_recovers_all_pain_points() -> None:
    fixture = load_fixture(EVAL_DIR / "whatsapp_hk.json")
    score = run_eval(fixture, provider="mock")
    assert score.personas_generated == 3
    assert score.expected_pain_points == 4
    assert score.recovered_pain_points == 4
    assert score.recovery_rate == 1.0
    assert score.unmatched_themes == []


def test_run_eval_handles_partial_recovery_when_personas_miss_themes() -> None:
    """Drop the spam-related pain-point claims from the mock response —
    the runner should mark the spam theme as unmatched."""
    fixture = load_fixture(EVAL_DIR / "whatsapp_hk.json")
    fixture["mock_persona_responses"]["wa_spam"]["pain_points"] = [
        {"claim": "Calls drop randomly", "severity": "high",
         "evidence": ["<post_005>"]},
    ]
    score = run_eval(fixture, provider="mock")
    assert "spam from strangers" in score.unmatched_themes
    assert score.recovered_pain_points < score.expected_pain_points


def test_run_eval_raises_when_mock_response_missing_for_a_cluster(tmp_path: Path) -> None:
    fixture = load_fixture(EVAL_DIR / "whatsapp_hk.json")
    fixture["mock_persona_responses"].pop("wa_calls", None)
    with pytest.raises(KeyError, match="missing mock response"):
        run_eval(fixture, provider="mock")


# ---------------------------------------------------------------------------
# Full suite
# ---------------------------------------------------------------------------


def test_run_eval_suite_aggregates_across_all_fixtures() -> None:
    report = run_eval_suite(provider="mock")
    assert len(report.scores) == 5
    assert report.mean_recovery_rate == 1.0
    # All 5 fixtures cover ≥1 source category, so mean coverage > 0.
    assert report.mean_coverage_score > 0


def test_run_eval_suite_with_custom_directory(tmp_path: Path) -> None:
    """Suite respects a directory override — useful for prompt-iteration
    branches that want to point at a different fixture set."""
    fixture = load_fixture(EVAL_DIR / "whatsapp_hk.json")
    (tmp_path / "whatsapp_hk.json").write_text(
        json.dumps(fixture), encoding="utf-8",
    )
    report = run_eval_suite(directory=tmp_path, provider="mock")
    assert len(report.scores) == 1
    assert report.scores[0].name == "whatsapp_hk"


def test_run_eval_suite_handles_empty_directory(tmp_path: Path) -> None:
    report = run_eval_suite(directory=tmp_path, provider="mock")
    assert report.scores == []
    assert report.mean_recovery_rate == 0.0
    assert report.mean_coverage_score == 0.0
