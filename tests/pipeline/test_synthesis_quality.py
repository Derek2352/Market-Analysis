"""Journey emotion semantics, coverage-aware confidence, richer evidence pack."""
from __future__ import annotations

from src.pipeline import synthesize as S
from src.render import journey_map as R
from src.schemas.cluster import Cluster


def test_prompt_emotion_labels_match_renderer():
    # A label the renderer doesn't classify plots as neutral, flattening the curve.
    assert set(S.JOURNEY_NEGATIVE_EMOTIONS) == R._NEGATIVE_EMOTIONS
    assert set(S.JOURNEY_POSITIVE_EMOTIONS) == R._POSITIVE_EMOTIONS


def test_journey_prompt_defines_intensity_as_strength():
    task = S._journey_task("Name", "one-liner")
    assert "NOT a positivity score" in task
    assert '"label": "frustrated", "intensity": 0.9' in task
    for label in S.JOURNEY_NEGATIVE_EMOTIONS + S.JOURNEY_POSITIVE_EMOTIONS:
        assert label in task


def test_confidence_capped_by_coverage_tier():
    assert S._compute_confidence(set(), "single-perspective") == 0.6
    assert S._compute_confidence(set(), "limited") == 0.75
    assert S._compute_confidence(set(), "balanced") == 0.9
    assert S._compute_confidence(set(), "high") == 1.0


def test_confidence_unverified_penalty_still_applies():
    assert S._compute_confidence({"goals", "behaviors"}, "high") == 0.8
    # Penalty below the cap wins; cap wins when grounding is better than breadth.
    assert S._compute_confidence({"a", "b", "c", "d", "e"}, "single-perspective") == 0.5
    assert S._compute_confidence(set(), None) == 1.0


def _cluster(**dists) -> Cluster:
    return Cluster(
        cluster_id="cluster_000", topic="t", region="HK", size=2,
        post_ids=["p1", "p2"], representative_post_ids=["p1"],
        keyword_summary=["app"], source_distribution={"app_store_hk": 2}, **dists,
    )


def test_evidence_pack_includes_sentiment_and_time_window():
    c = _cluster(
        language_distribution={"yue": 2},
        sentiment_distribution={"negative": 2},
        temporal_distribution={"2025-05": 1, "2026-09": 1},
    )
    pack = S._build_evidence_pack(c, {"p1": "a", "p2": "b"}, None, "HK")
    assert 'sentiment from star ratings' in pack.block_text
    assert '{"negative": 2}' in pack.block_text
    assert "posted between 2025-05 and 2026-09 (2 months with posts)" in pack.block_text


def test_evidence_pack_omits_empty_distributions():
    pack = S._build_evidence_pack(_cluster(), {"p1": "a", "p2": "b"}, None, "HK")
    assert "sentiment from star ratings" not in pack.block_text
    assert "posted between" not in pack.block_text


def test_persona_ids_are_scoped_to_the_clustering_run():
    """Cluster ids repeat in every run of a topic and personas share one
    folder per topic/region: an id from the cluster id alone let a new run
    overwrite an older run's persona file."""
    from src.pipeline.synthesize import _persona_id

    assert _persona_id("cluster_000", "R1") == _persona_id("cluster_000", "R1")  # re-runs are idempotent
    assert _persona_id("cluster_000", "R1") != _persona_id("cluster_000", "R2")
    assert _persona_id("cluster_000", "R1") != _persona_id("cluster_001", "R1")
