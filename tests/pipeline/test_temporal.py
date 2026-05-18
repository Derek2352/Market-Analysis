"""Temporal trend analysis tests — bucketing, spikes, CLI flag."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()


# ---------------------------------------------------------------------------
# compute_temporal_trends tests
# ---------------------------------------------------------------------------

class TestTemporalTrends:

    def test_empty_metadata_returns_empty(self):
        from src.pipeline.models import compute_temporal_trends
        trends = compute_temporal_trends({}, topic="test", region="HK")
        assert trends.total_posts == 0
        assert len(trends.buckets) == 0
        assert len(trends.spikes) == 0

    def test_weekly_bucketing(self):
        from src.pipeline.models import compute_temporal_trends

        # 4 posts: 2 in week 1, 2 in week 2
        base = datetime(2026, 5, 4, tzinfo=timezone.utc)  # Monday
        metadata = {}
        for i in range(4):
            offset_days = i * 7 if i < 2 else (i % 2) * 7 + 7
            dt = base + timedelta(days=offset_days)
            pid = f"post_{i}"
            metadata[pid] = {
                "posted_at": dt.isoformat(),
                "body": f"Post {i} content",
                "source": "test",
            }

        trends = compute_temporal_trends(metadata, topic="test", region="HK", bucket_type="week")
        assert trends.bucket_type == "week"
        assert trends.total_posts == 4
        assert len(trends.buckets) >= 2
        assert sum(b.post_count for b in trends.buckets) == 4

    def test_monthly_bucketing(self):
        from src.pipeline.models import compute_temporal_trends

        metadata = {}
        for i in range(3):
            dt = datetime(2026, i + 1, 15, tzinfo=timezone.utc)
            pid = f"post_{i}"
            metadata[pid] = {
                "posted_at": dt.isoformat(),
                "body": f"Post {i}",
                "source": "test",
            }

        trends = compute_temporal_trends(metadata, topic="test", region="HK", bucket_type="month")
        assert trends.bucket_type == "month"
        assert trends.total_posts == 3
        assert len(trends.buckets) == 3  # Jan, Feb, Mar

    def test_complaint_detection(self):
        from src.pipeline.models import compute_temporal_trends

        dt = datetime(2026, 5, 1, tzinfo=timezone.utc)
        metadata = {
            "p1": {"posted_at": dt.isoformat(), "body": "This app is terrible and broken", "source": "test"},
            "p2": {"posted_at": dt.isoformat(), "body": "Great app, love it", "source": "test"},
            "p3": {"posted_at": dt.isoformat(), "body": "Expensive and frustrating", "source": "test"},
        }

        trends = compute_temporal_trends(metadata, topic="test", region="HK")
        assert trends.total_posts == 3
        assert len(trends.buckets) == 1
        assert trends.buckets[0].complaint_count == 2  # terrible/broken + expensive/frustrating
        assert trends.buckets[0].sentiment_score < 0

    def test_spike_detection(self):
        from src.pipeline.models import compute_temporal_trends

        base = datetime(2026, 5, 1, tzinfo=timezone.utc)
        metadata = {}
        # 4 weeks: 1, 1, 20, 1 posts
        counts = [1, 1, 20, 1]
        for week_idx, count in enumerate(counts):
            for i in range(count):
                pid = f"w{week_idx}_p{i}"
                dt = base + timedelta(weeks=week_idx, hours=i)
                metadata[pid] = {
                    "posted_at": dt.isoformat(),
                    "body": "test post",
                    "source": "test",
                }

        trends = compute_temporal_trends(metadata, topic="test", region="HK")
        assert len(trends.spikes) == 1
        assert trends.spikes[0]["post_count"] == 20
        assert trends.spikes[0]["bucket"].startswith("2026-W")

    def test_to_dict_serializable(self):
        from src.pipeline.models import compute_temporal_trends

        dt = datetime(2026, 5, 1, tzinfo=timezone.utc)
        metadata = {
            "p1": {"posted_at": dt.isoformat(), "body": "test", "source": "test"},
        }
        trends = compute_temporal_trends(metadata, topic="test", region="HK")
        d = trends.to_dict()
        assert isinstance(d, dict)
        assert d["topic"] == "test"
        assert d["region"] == "HK"
        # Verify JSON serializable
        json.dumps(d)


# ---------------------------------------------------------------------------
# CLI temporal flag tests
# ---------------------------------------------------------------------------

class TestTemporalCLI:

    def test_help_shows_temporal_flag(self):
        """mkt synthesize --help should show --temporal."""
        result = runner.invoke(
            __import__("src.cli", fromlist=["app"]).app,
            ["synthesize", "--help"],
        )
        assert result.exit_code == 0
        assert "--temporal" in result.output

    def test_temporal_flag_accepted_with_no_data(self, tmp_path: Path, monkeypatch):
        """--temporal should not crash when there's no data loaded yet."""
        import json as _json

        data_dir = tmp_path / "data"
        monkeypatch.setattr("src.cli._DATA_DIR", data_dir)

        # Create a single post in raw data (needed by synthesize)
        raw_dir = data_dir / "raw" / "test_temporal" / "HK"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_posts = [{
            "id": "p1", "source": "test", "source_category": "FORUMS",
            "region": "HK", "language": "en",
            "url": "https://example.com/1", "author_hash": "abc",
            "title": "Test", "body": "Test body content",
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "signal_type": "OPINION", "engagement_metrics": {},
        }]
        (_raw_dir := data_dir / "raw" / "test_temporal" / "HK")
        _raw_dir.mkdir(parents=True, exist_ok=True)
        (_raw_dir / "run_01.json").write_text(_json.dumps(raw_posts))

        # Create minimal cluster file referencing p1
        clusters_dir = data_dir / "clusters" / "test_temporal" / "HK"
        clusters_dir.mkdir(parents=True, exist_ok=True)
        cluster_data = {
            "topic": "test_temporal",
            "region": "HK",
            "total_posts": 1,
            "noise_count": 0,
            "clusters": [{
                "cluster_id": "c1",
                "topic": "test_temporal",
                "region": "HK",
                "size": 1,
                "post_ids": ["p1"],
                "keyword_summary": ["test"],
            }],
            "params": {},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        (clusters_dir / "clusters_20260101.json").write_text(_json.dumps(cluster_data))

        result = runner.invoke(
            __import__("src.cli", fromlist=["app"]).app,
            ["synthesize", "--topic", "test_temporal", "--region", "HK",
             "--temporal", "--dry-run"],
        )
        # Dry-run should succeed; temporal analysis runs before LLM
        assert result.exit_code == 0, result.output
