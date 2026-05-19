"""Scrape-doctor tests — health-check CLI, scraper validation, last-scrape detection."""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.cli import app as _cli_app

runner = CliRunner()


# ---------------------------------------------------------------------------
# _check_scraper unit tests (mocked scrapers)
# ---------------------------------------------------------------------------

class TestCheckScraper:
    """Tests for _check_scraper — pass, fail, init error, and edge cases."""

    def test_scraper_passes(self) -> None:
        """Healthy scraper returns status=pass with post count."""
        from src.cli_doctor import _check_scraper

        mock_scraper = MagicMock()
        mock_scraper.search.return_value = iter([{"id": "1"}, {"id": "2"}])

        with patch("src.cli_doctor.get_scraper", return_value=mock_scraper):
            result = _check_scraper("test_source")

        assert result["source"] == "test_source"
        assert result["status"] == "pass"
        assert result["posts_found"] == 2
        assert result["error"] is None
        assert result["elapsed_ms"] >= 0

    def test_scraper_returns_zero_posts(self) -> None:
        """Empty results still count as pass (scraper responded, just no data)."""
        from src.cli_doctor import _check_scraper

        mock_scraper = MagicMock()
        mock_scraper.search.return_value = iter([])

        with patch("src.cli_doctor.get_scraper", return_value=mock_scraper):
            result = _check_scraper("empty_source")

        assert result["status"] == "pass"
        assert result["posts_found"] == 0

    def test_scraper_sourceerror(self) -> None:
        """SourceError from scraper.search → status=fail with truncated error."""
        from src.cli_doctor import _check_scraper
        from src.scrape.base import SourceError

        mock_scraper = MagicMock()
        mock_scraper.search.side_effect = SourceError("HTTP 403 — blocked by Cloudflare")

        with patch("src.cli_doctor.get_scraper", return_value=mock_scraper):
            result = _check_scraper("blocked_source")

        assert result["status"] == "fail"
        assert "403" in result["error"]
        assert result["posts_found"] == 0

    def test_scraper_unexpected_exception(self) -> None:
        """Unexpected exception → status=fail, error contains exception name."""
        from src.cli_doctor import _check_scraper

        mock_scraper = MagicMock()
        mock_scraper.search.side_effect = RuntimeError("something exploded")

        with patch("src.cli_doctor.get_scraper", return_value=mock_scraper):
            result = _check_scraper("broken_source")

        assert result["status"] == "fail"
        assert "RuntimeError" in result["error"]
        assert "something exploded" in result["error"]

    def test_scraper_init_error(self) -> None:
        """get_scraper raises → fail with init error before any search."""
        from src.cli_doctor import _check_scraper

        with patch("src.cli_doctor.get_scraper", side_effect=ImportError("no module")):
            result = _check_scraper("missing_dep")

        assert result["status"] == "fail"
        assert "init error" in result["error"]
        assert "no module" in result["error"]
        assert result["elapsed_ms"] >= 0

    def test_elapsed_time_measured(self) -> None:
        """elapsed_ms is positive and reflects actual time taken."""
        from src.cli_doctor import _check_scraper

        mock_scraper = MagicMock()

        def slow_search(*args, **kwargs):
            time.sleep(0.05)
            return iter([{"id": "1"}])

        mock_scraper.search.side_effect = slow_search

        with patch("src.cli_doctor.get_scraper", return_value=mock_scraper):
            result = _check_scraper("slow_source")

        assert result["elapsed_ms"] >= 40  # at least 40ms given 50ms sleep+overhead

    def test_scraper_close_called(self) -> None:
        """close() is called if scraper has it, even on error."""
        from src.cli_doctor import _check_scraper

        mock_scraper = MagicMock()
        mock_scraper.search.side_effect = RuntimeError("boom")

        with patch("src.cli_doctor.get_scraper", return_value=mock_scraper):
            _check_scraper("closing_source")

        mock_scraper.close.assert_called_once()

    def test_close_missing_does_not_crash(self) -> None:
        """Scrapers without a close() method don't cause errors."""
        from src.cli_doctor import _check_scraper

        mock_scraper = MagicMock(spec=["search"])  # no close attribute
        mock_scraper.search.return_value = iter([{"id": "1"}])

        with patch("src.cli_doctor.get_scraper", return_value=mock_scraper):
            result = _check_scraper("no_close_source")

        assert result["status"] == "pass"


# ---------------------------------------------------------------------------
# _get_last_scrape_ts unit tests
# ---------------------------------------------------------------------------

class TestGetLastScrapeTs:
    """Tests for _get_last_scrape_ts — finding the latest scrape timestamp."""

    def test_returns_none_when_no_data_dir(self, tmp_path: Path) -> None:
        """No data/raw directory → returns None."""
        from src.cli_doctor import _get_last_scrape_ts, _DATA_DIR

        with patch("src.cli_doctor._DATA_DIR", tmp_path / "nonexistent"):
            result = _get_last_scrape_ts("lihkg")
            assert result is None

    def test_finds_latest_among_multiple_runs(self, tmp_path: Path) -> None:
        """Picks the most recent file modification time across topics/regions."""
        from src.cli_doctor import _get_last_scrape_ts, _DATA_DIR

        raw_dir = tmp_path / "raw" / "alipayhk" / "HK"
        raw_dir.mkdir(parents=True)

        # Older file
        old_file = raw_dir / "old_run.json"
        old_file.write_text(json.dumps([
            {"source": "lihkg", "id": "old", "body": "old post"}
        ]))
        # Set older mtime
        old_ts = time.time() - 86400  # 1 day ago
        old_file.touch()
        import os
        os.utime(str(old_file), (old_ts, old_ts))

        # Newer file in different region
        new_dir = tmp_path / "raw" / "octopus" / "TW"
        new_dir.mkdir(parents=True)
        new_file = new_dir / "new_run.json"
        new_file.write_text(json.dumps([
            {"source": "lihkg", "id": "new", "body": "new post"}
        ]))

        with patch("src.cli_doctor._DATA_DIR", tmp_path):
            result = _get_last_scrape_ts("lihkg")

        assert result is not None
        assert "UTC" in result

    def test_returns_none_when_no_matching_source(self, tmp_path: Path) -> None:
        """Scraper not present in any data file → returns None."""
        from src.cli_doctor import _get_last_scrape_ts, _DATA_DIR

        raw_dir = tmp_path / "raw" / "testtopic" / "HK"
        raw_dir.mkdir(parents=True)
        run_file = raw_dir / "run.json"
        run_file.write_text(json.dumps([
            {"source": "other_source", "id": "1", "body": "irrelevant"}
        ]))

        with patch("src.cli_doctor._DATA_DIR", tmp_path):
            result = _get_last_scrape_ts("lihkg")

        assert result is None

    def test_ignores_run_sidecar_files(self, tmp_path: Path) -> None:
        """Files ending in ._run.json are skipped (sidecar metadata)."""
        from src.cli_doctor import _get_last_scrape_ts, _DATA_DIR

        raw_dir = tmp_path / "raw" / "testtopic" / "HK"
        raw_dir.mkdir(parents=True)

        # Sidecar file — should be ignored
        sidecar = raw_dir / "something._run.json"
        sidecar.write_text(json.dumps([
            {"source": "lihkg", "id": "sidecar", "body": "should be ignored"}
        ]))

        with patch("src.cli_doctor._DATA_DIR", tmp_path):
            result = _get_last_scrape_ts("lihkg")

        assert result is None

    def test_handles_corrupt_json_gracefully(self, tmp_path: Path) -> None:
        """Corrupt JSON files are skipped without crashing."""
        from src.cli_doctor import _get_last_scrape_ts, _DATA_DIR

        raw_dir = tmp_path / "raw" / "testtopic" / "HK"
        raw_dir.mkdir(parents=True)

        # Corrupt file
        (raw_dir / "bad.json").write_text("not valid json {{{")

        # Valid file with matching source
        good = raw_dir / "good.json"
        good.write_text(json.dumps([
            {"source": "lihkg", "id": "good", "body": "valid"}
        ]))

        with patch("src.cli_doctor._DATA_DIR", tmp_path):
            result = _get_last_scrape_ts("lihkg")

        assert result is not None  # found the good file
        assert "UTC" in result


# ---------------------------------------------------------------------------
# CLI integration tests (via CliRunner)
# ---------------------------------------------------------------------------

class TestDoctorCLI:
    """Integration tests for the CLI `mkt doctor` command."""

    def test_doctor_help_output(self) -> None:
        """--help shows usage information."""
        result = runner.invoke(_cli_app, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "Health-check" in result.stdout
        assert "--source" in result.stdout
        assert "--json" in result.stdout
        assert "--skip-slow" in result.stdout

    def test_doctor_unknown_source_exits_2(self) -> None:
        """Passing a non-existent source → exit code 2 with error."""
        result = runner.invoke(_cli_app, ["doctor", "--source", "nonexistent_scraper_xyz"])
        assert result.exit_code == 2
        assert "Unknown source" in result.stderr or "Unknown source" in result.stdout

    def test_doctor_json_flag_produces_valid_json(self) -> None:
        """--json produces parseable JSON with expected keys."""
        # We mock _check_scraper so no real network calls happen
        mock_results = [
            {
                "source": "lihkg",
                "status": "pass",
                "error": None,
                "posts_found": 5,
                "elapsed_ms": 120,
                "last_scrape": "2026-05-19 03:00 UTC",
            },
        ]

        with patch("src.cli_doctor.available_sources", return_value=["lihkg"]), \
             patch("src.cli_doctor._check_scraper", return_value=mock_results[0]):
            result = runner.invoke(_cli_app, ["doctor", "--json"])

        assert result.exit_code == 0
        # JSON is printed after progress lines; find where it starts
        lines = result.stdout.strip().split("\n")
        json_start = next(i for i, ln in enumerate(lines) if ln.strip() == "[")
        json_text = "\n".join(lines[json_start:])
        data = json.loads(json_text)
        assert isinstance(data, list)
        assert data[0]["source"] == "lihkg"
        assert data[0]["status"] == "pass"

    def test_doctor_single_source_success(self) -> None:
        """Checking one known source returns exit 0 on pass."""
        mock_result = {
            "source": "lihkg",
            "status": "pass",
            "error": None,
            "posts_found": 3,
            "elapsed_ms": 45,
            "last_scrape": None,
        }

        with patch("src.cli_doctor._check_scraper", return_value=mock_result):
            result = runner.invoke(_cli_app, ["doctor", "--source", "lihkg"])

        assert result.exit_code == 0
        assert "PASS" in result.stdout
        assert "lihkg" in result.stdout

    def test_doctor_single_source_failure_exits_1(self) -> None:
        """Failing scraper → exit code 1."""
        mock_result = {
            "source": "discuss_hk",
            "status": "fail",
            "error": "HTTP 503 Service Unavailable",
            "posts_found": 0,
            "elapsed_ms": 5000,
            "last_scrape": None,
        }

        with patch("src.cli_doctor._check_scraper", return_value=mock_result):
            result = runner.invoke(_cli_app, ["doctor", "--source", "discuss_hk"])

        assert result.exit_code == 1
        assert "FAIL" in result.stdout

    def test_doctor_skip_slow_flag(self) -> None:
        """--skip-slow excludes Playwright-based scrapers from the list."""
        with patch("src.cli_doctor.available_sources", return_value=[
            "lihkg", "yelp_html", "openrice", "app_store_hk"
        ]), patch("src.cli_doctor._check_scraper", return_value={
            "source": "lihkg", "status": "pass", "error": None,
            "posts_found": 1, "elapsed_ms": 10, "last_scrape": None,
        }):
            result = runner.invoke(_cli_app, ["doctor", "--skip-slow"])

        assert result.exit_code == 0
        # yelp_html and openrice should be skipped
        assert "yelp_html" in result.stdout  # mentioned as skipped
        assert "openrice" in result.stdout
        # Only 2 scrapers actually checked (lihkg, app_store_hk)
        assert "Checking 2 scrapers" in result.stdout
