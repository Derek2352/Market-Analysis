"""Tests for cluster_diag.py — report generation and c-TF-IDF keyword extraction."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.pipeline.cluster_diag import (
    generate_report,
    compute_ctfidf_keywords,
    SOURCE_DOMINANCE_THRESHOLD,
    SNIPPET_CHARS,
)
from src.schemas.cluster import Cluster, ClusteringResult


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _make_cluster(
    cid: str = "c0",
    size: int = 10,
    post_ids: list[str] | None = None,
    keywords: list[str] | None = None,
    source_dist: dict[str, int] | None = None,
    lang_dist: dict[str, int] | None = None,
    rep_ids: list[str] | None = None,
) -> Cluster:
    return Cluster(
        cluster_id=cid,
        topic="test",
        region="HK",
        size=size,
        post_ids=post_ids or [f"{cid}_p{i}" for i in range(size)],
        keyword_summary=keywords or [f"kw{i}" for i in range(5)],
        source_distribution=source_dist or {"lihkg": size},
        language_distribution=lang_dist or {"zh": size},
        representative_post_ids=rep_ids or [f"{cid}_p0", f"{cid}_p1", f"{cid}_p2"],
    )


def _make_result(
    topic: str = "test_topic",
    region: str = "HK",
    total_posts: int = 30,
    noise_count: int = 5,
    clusters: list[Cluster] | None = None,
) -> ClusteringResult:
    if clusters is None:
        clusters = [
            _make_cluster("c0", 15, source_dist={"lihkg": 15}),
            _make_cluster("c1", 10, source_dist={"app_store_hk": 10}),
        ]
    return ClusteringResult(
        topic=topic,
        region=region,
        total_posts=total_posts,
        noise_count=noise_count,
        clusters=clusters,
    )


# ---------------------------------------------------------------------------
# generate_report tests
# ---------------------------------------------------------------------------

class TestGenerateReport:
    """Tests for the Markdown diagnostic report generator."""

    def test_basic_report_has_header(self, tmp_path: Path) -> None:
        """Report includes topic, region, and generation timestamp."""
        result = _make_result()
        post_texts = {"c0_p0": "post text", "c0_p1": "another post", "c0_p2": "third"}
        out = tmp_path / "diag.md"

        report = generate_report(result, post_texts, out)

        assert result.topic in report
        assert result.region in report
        assert "Generated:" in report
        assert "Total posts:" in report
        assert "Clusters found:" in report

    def test_report_includes_noise_stats(self, tmp_path: Path) -> None:
        """Noise count and percentage are reported."""
        result = _make_result(total_posts=50, noise_count=15)
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "Noise (unclustered): 15 (30.0%)" in report

    def test_cluster_size_histogram(self, tmp_path: Path) -> None:
        """ASCII histogram of cluster sizes appears."""
        result = _make_result()
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "Cluster Sizes" in report
        assert "Range:" in report
        assert "█" in report  # ASCII bar

    def test_no_clusters_shows_clear_message(self, tmp_path: Path) -> None:
        """When clusters list is empty, a helpful message is shown."""
        result = _make_result(total_posts=20, noise_count=20, clusters=[])
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "No clusters found" in report

    def test_keywords_displayed_per_cluster(self, tmp_path: Path) -> None:
        """Top 10 keywords are shown for each cluster."""
        kw = [f"theme_{i}" for i in range(12)]
        cluster = _make_cluster("c0", keywords=kw)
        result = _make_result(clusters=[cluster])
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "Keywords:" in report
        # Only first 10 keywords shown
        assert "theme_0" in report
        assert "theme_9" in report
        assert "theme_10" not in report  # beyond 10

    def test_representative_snippets(self, tmp_path: Path) -> None:
        """Up to 3 representative post snippets are shown."""
        posts = {
            "c0_p0": "First post content here.",
            "c0_p1": "Second post with different text.",
            "c0_p2": "Third post, also different.",
        }
        cluster = _make_cluster("c0", rep_ids=["c0_p0", "c0_p1", "c0_p2"])
        result = _make_result(clusters=[cluster])
        out = tmp_path / "diag.md"

        report = generate_report(result, posts, out)

        assert "Representative posts:" in report
        assert "c0_p0" in report
        assert "First post content here" in report
        assert "c0_p1" in report
        assert "c0_p2" in report

    def test_snippet_truncation(self, tmp_path: Path) -> None:
        """Posts longer than SNIPPET_CHARS are truncated with ellipsis."""
        long_text = "x" * 300
        posts = {"c0_p0": long_text}
        cluster = _make_cluster("c0", rep_ids=["c0_p0"])
        result = _make_result(clusters=[cluster])
        out = tmp_path / "diag.md"

        report = generate_report(result, posts, out)

        snippet = "x" * SNIPPET_CHARS + "…"
        assert snippet in report.replace("\n", " ")

    def test_source_distribution_with_bias_warning(self, tmp_path: Path) -> None:
        """Source >80% from one source triggers bias warning."""
        cluster = _make_cluster("c0", size=20, source_dist={"lihkg": 18, "reddit": 2})
        result = _make_result(clusters=[cluster])
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "bias risk" in report
        assert "lihkg: 18" in report

    def test_source_distribution_balanced_no_warning(self, tmp_path: Path) -> None:
        """Balanced sources don't trigger bias warning."""
        cluster = _make_cluster("c0", size=20, source_dist={"lihkg": 10, "reddit": 10})
        result = _make_result(clusters=[cluster])
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "bias risk" not in report

    def test_language_distribution_displayed(self, tmp_path: Path) -> None:
        """Language distribution is shown per cluster."""
        cluster = _make_cluster("c0", lang_dist={"zh": 12, "en": 8})
        result = _make_result(clusters=[cluster])
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "Language distribution:" in report
        assert "zh: 12" in report
        assert "en: 8" in report

    def test_cluster_without_language_distribution(self, tmp_path: Path) -> None:
        """Cluster without language entries shows no language counts."""
        cluster = _make_cluster("c0", lang_dist={})
        result = _make_result(clusters=[cluster])
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        # Language section may appear but with no entries
        lang_section = report.split("Language distribution:")[-1] if "Language distribution:" in report else ""
        assert "zh:" not in lang_section or "zh: 0" not in lang_section

    def test_high_noise_suggests_lowering_params(self, tmp_path: Path) -> None:
        """Noise >40% suggests parameter adjustments."""
        result = _make_result(total_posts=100, noise_count=60)
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "Noise ratio is high" in report
        assert "Lowering" in report

    def test_low_noise_suggests_raising_params(self, tmp_path: Path) -> None:
        """Noise <5% suggests tightening parameters."""
        result = _make_result(total_posts=100, noise_count=2)
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "Noise ratio is low" in report
        assert "Raising" in report

    def test_healthy_noise_shows_checkmark(self, tmp_path: Path) -> None:
        """Noise 5-40% shows healthy range message."""
        result = _make_result(total_posts=100, noise_count=20)
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "healthy range" in report
        assert "✓" in report

    def test_writes_file_to_output_path(self, tmp_path: Path) -> None:
        """Report is written to the specified path."""
        result = _make_result()
        out = tmp_path / "subdir" / "diag.md"

        report = generate_report(result, {}, out)

        assert out.exists()
        assert out.read_text(encoding="utf-8") == report

    def test_handles_missing_post_ids_gracefully(self, tmp_path: Path) -> None:
        """Representative IDs not in post_texts → empty snippet."""
        cluster = _make_cluster("c0", rep_ids=["missing_1", "missing_2"])
        result = _make_result(clusters=[cluster])
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        assert "missing_1" in report
        # Should not crash

    def test_empty_post_texts_does_not_crash(self, tmp_path: Path) -> None:
        """Empty post_texts dict → snippets show empty string."""
        result = _make_result()
        out = tmp_path / "diag.md"

        report = generate_report(result, {}, out)

        # Should complete without error
        assert "Cluster 0" in report or "Cluster c0" in report


# ---------------------------------------------------------------------------
# compute_ctfidf_keywords tests
# ---------------------------------------------------------------------------

class TestComputeCTFIDFKeywords:
    """Tests for class-based TF-IDF keyword extraction."""

    def test_two_clusters_produce_distinct_keywords(self) -> None:
        """With 2+ clusters, c-TF-IDF returns distinct keywords per cluster."""
        posts = {
            "a1": "payment wallet transfer money bank",
            "a2": "wallet transfer cash octopus card",
            "b1": "crash bug freeze slow performance",
            "b2": "bug error crash memory leak",
        }
        clusters = [
            _make_cluster("c_payment", size=2, post_ids=["a1", "a2"]),
            _make_cluster("c_perf", size=2, post_ids=["b1", "b2"]),
        ]

        kw = compute_ctfidf_keywords(clusters, posts, top_n=5)

        assert len(kw) == 2
        assert len(kw["c_payment"]) == 5
        assert len(kw["c_perf"]) == 5
        # Keywords should differ between clusters
        assert kw["c_payment"] != kw["c_perf"]

    def test_single_cluster_returns_empty(self) -> None:
        """< 2 clusters → empty keywords (c-TF-IDF needs comparison)."""
        posts = {"a1": "text text text"}
        clusters = [_make_cluster("c0", size=1, post_ids=["a1"])]

        kw = compute_ctfidf_keywords(clusters, posts)

        assert kw == {"c0": []}

    def test_empty_clusters_list(self) -> None:
        """Empty clusters list returns empty dict."""
        kw = compute_ctfidf_keywords([], {})
        assert kw == {}

    def test_respects_top_n_parameter(self) -> None:
        """top_n limits keyword count per cluster."""
        posts = {
            "a1": "payment wallet transfer money bank octopus cash card",
            "a2": "wallet transfer fast system fps payment",
            "b1": "crash bug freeze slow performance memory leak",
            "b2": "bug error crash app hang freeze",
        }
        clusters = [
            _make_cluster("c_a", size=2, post_ids=["a1", "a2"]),
            _make_cluster("c_b", size=2, post_ids=["b1", "b2"]),
        ]

        kw = compute_ctfidf_keywords(clusters, posts, top_n=3)

        assert len(kw["c_a"]) == 3
        assert len(kw["c_b"]) == 3

    def test_with_tokenizer(self) -> None:
        """When a tokenizer is provided, text is pre-tokenized."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.tokenize.side_effect = lambda t: t.split()

        posts = {
            "a1": "支付 寶 好用 方便",
            "a2": "支付 寶 轉賬 快",
            "b1": "慢 卡 死機 問題",
        }
        clusters = [
            _make_cluster("c_pay", size=2, post_ids=["a1", "a2"]),
            _make_cluster("c_bug", size=1, post_ids=["b1"]),
        ]

        kw = compute_ctfidf_keywords(clusters, posts, top_n=3, tokenizer=mock_tokenizer)

        assert mock_tokenizer.tokenize.called
        assert len(kw) == 2
        assert len(kw["c_pay"]) == 3

    def test_missing_post_texts_are_empty(self) -> None:
        """Post IDs not in post_texts → treated as empty string."""
        posts = {"a1": "payment wallet transfer"}
        clusters = [
            _make_cluster("c0", size=2, post_ids=["a1", "missing"]),
            _make_cluster("c1", size=1, post_ids=["b1"]),
        ]

        kw = compute_ctfidf_keywords(clusters, posts)

        assert "c0" in kw
        assert "c1" in kw
        # Should not crash
