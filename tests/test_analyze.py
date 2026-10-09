"""Smoke tests for mkt analyze — CLI arg validation, help output, error paths.

These tests validate the CLI interface without running real scrapers or models.
"""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from src.cli import app as _cli_app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Help and usage
# ---------------------------------------------------------------------------

class TestAnalyzeHelp:
    def test_help_output(self) -> None:
        """--help shows usage with all options."""
        result = runner.invoke(_cli_app, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "--topic" in result.stdout
        assert "--region" in result.stdout
        assert "--sources" in result.stdout
        assert "--limit" in result.stdout
        assert "--since" in result.stdout
        assert "--personas" in result.stdout
        assert "--subreddits" in result.stdout

    def test_help_describes_pipeline(self) -> None:
        """Help text mentions scrape/embed/cluster."""
        result = runner.invoke(_cli_app, ["analyze", "--help"])
        assert "scrape" in result.stdout.lower()
        assert "pipeline" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Missing required args
# ---------------------------------------------------------------------------

class TestAnalyzeMissingArgs:
    def test_missing_topic_exits_2(self) -> None:
        """--topic is required; missing it → exit 2."""
        result = runner.invoke(_cli_app, ["analyze", "--region", "HK"])
        assert result.exit_code == 2

    def test_missing_region_exits_2(self) -> None:
        """--region is required; missing it → exit 2."""
        result = runner.invoke(_cli_app, ["analyze", "--topic", "TestApp"])
        assert result.exit_code == 2

    def test_missing_both_exits_2(self) -> None:
        """No args → exit 2."""
        result = runner.invoke(_cli_app, ["analyze"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

class TestAnalyzeArgValidation:
    def test_limit_must_be_positive(self) -> None:
        """--limit 0 should fail (min=1)."""
        result = runner.invoke(_cli_app, [
            "analyze", "--topic", "Test", "--region", "HK", "--limit", "0",
        ])
        assert result.exit_code != 0

    def test_unknown_source_exits_2(self) -> None:
        """Passing a non-existent source → exit 2 with error message."""
        result = runner.invoke(_cli_app, [
            "analyze", "--topic", "Test", "--region", "HK",
            "--sources", "nonexistent_source_xyz",
        ])
        assert result.exit_code == 2
        assert "Unknown source" in result.stderr or "Unknown source" in result.stdout

    def test_valid_minimal_args_accepted(self) -> None:
        """Minimal valid args (topic + region) should parse without error on
        arg validation. Pipeline will fail downstream when deps/scrapers
        aren't available, but arg parsing itself should succeed.

        We mock _PIPELINE_AVAILABLE=False to trigger the early-exit
        error path (exit 1), which proves arg parsing passed.
        """
        with patch("src.cli._PIPELINE_AVAILABLE", False):
            result = runner.invoke(_cli_app, [
                "analyze", "--topic", "TestApp", "--region", "HK",
            ])
        assert result.exit_code == 1
        assert "Pipeline deps" in result.stderr or "deps" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Flag combinations
# ---------------------------------------------------------------------------

class TestAnalyzeFlags:
    def test_personas_flag_accepted(self) -> None:
        """--personas flag is recognized without error."""
        with patch("src.cli._PIPELINE_AVAILABLE", False):
            result = runner.invoke(_cli_app, [
                "analyze", "--topic", "Test", "--region", "HK", "--personas",
            ])
        assert result.exit_code == 1  # pipeline deps not available

    def test_no_personas_flag_accepted(self) -> None:
        """--no-personas flag is recognized (default)."""
        with patch("src.cli._PIPELINE_AVAILABLE", False):
            result = runner.invoke(_cli_app, [
                "analyze", "--topic", "Test", "--region", "HK", "--no-personas",
            ])
        assert result.exit_code == 1

    def test_subreddits_flag_accepted(self) -> None:
        """--subreddits flag is recognized."""
        with patch("src.cli._PIPELINE_AVAILABLE", False):
            result = runner.invoke(_cli_app, [
                "analyze", "--topic", "Test", "--region", "HK",
                "--subreddits", "HongKong,China",
            ])
        assert result.exit_code == 1

    def test_since_flag_accepted(self) -> None:
        """--since flag accepts relative time window."""
        with patch("src.cli._PIPELINE_AVAILABLE", False):
            result = runner.invoke(_cli_app, [
                "analyze", "--topic", "Test", "--region", "HK",
                "--since", "30d",
            ])
        assert result.exit_code == 1

    def test_limit_flag_accepted(self) -> None:
        """--limit flag accepts integer."""
        with patch("src.cli._PIPELINE_AVAILABLE", False):
            result = runner.invoke(_cli_app, [
                "analyze", "--topic", "Test", "--region", "HK",
                "--limit", "50",
            ])
        assert result.exit_code == 1

    def test_sources_flag_accepted(self) -> None:
        """--sources flag with valid sources parses correctly."""
        with patch("src.cli._PIPELINE_AVAILABLE", False):
            result = runner.invoke(_cli_app, [
                "analyze", "--topic", "Test", "--region", "HK",
                "--sources", "app_store_hk",
            ])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# A source failing mid-run
# ---------------------------------------------------------------------------

class TestAnalyzeSourceFailure:
    def test_posts_yielded_before_an_error_are_written(self, tmp_path, monkeypatch) -> None:
        """A source that raises mid-iteration (e.g. OpenRice's bot check) must
        not lose the posts it already yielded: they are marked seen in the
        dedup index, so a later run would skip them as duplicates."""
        import json
        from datetime import datetime, timezone

        from src import cli
        from src.schemas.raw import RawPost
        from src.scrape.base.protocol import SourceError
        from src.scrape.utils.dedup import DedupIndex

        def post(i: int) -> RawPost:
            return RawPost(
                id=f"p{i}", source="openrice", source_category="reviews", region="HK",
                language="zh-HK", url=f"https://example.com/{i}", author_hash="0" * 64,
                body="b", posted_at=datetime.now(timezone.utc), signal_type="experience",
            )

        class _FailsMidRun:
            def search(self, topic, *, since, limit):
                yield post(1)
                yield post(2)
                raise SourceError("security check did not clear")

            def close(self) -> None:
                pass

        class _NoEmbeddings:
            def __init__(self, *a, **k) -> None:
                pass

            def embed_posts(self, posts, **k) -> int:
                return 0

            def close(self) -> None:
                pass

        monkeypatch.setenv("AUTHOR_HASH_SALT", "t")
        monkeypatch.setattr(cli, "_DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(cli, "_ROOT", tmp_path)
        monkeypatch.setattr(cli, "get_scraper", lambda sid, **kw: _FailsMidRun())
        monkeypatch.setattr(cli, "EmbeddingStore", _NoEmbeddings)

        result = runner.invoke(
            _cli_app,
            ["analyze", "--topic", "x", "--region", "HK", "--sources", "openrice", "--since", "30d"],
        )
        assert "then failed: security check did not clear" in result.output
        files = [f for f in (tmp_path / "data" / "raw" / "x" / "HK").glob("openrice_*.json")
                 if not f.name.endswith("._run.json")]
        assert len(files) == 1
        assert [p["id"] for p in json.loads(files[0].read_text(encoding="utf-8"))] == ["p1", "p2"]
        with DedupIndex(tmp_path / "data" / "dedup.sqlite") as index:
            assert index.is_seen("openrice", "p1")
