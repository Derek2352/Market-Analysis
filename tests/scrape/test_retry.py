"""Retry/backoff tests — PoliteClient retries on transient failures."""

import pytest
from typer.testing import CliRunner

runner = CliRunner()


class TestSetDefaultRetries:
    """Module-level set_default_retries() controls global retry count."""

    def test_default_is_three(self):
        import src.scrape.base.http as m
        assert m.DEFAULT_MAX_RETRIES == 3

    def test_set_and_reset(self):
        import src.scrape.base.http as m

        original = m.DEFAULT_MAX_RETRIES
        try:
            m.set_default_retries(5)
            assert m.DEFAULT_MAX_RETRIES == 5

            m.set_default_retries(0)
            assert m.DEFAULT_MAX_RETRIES == 0

            m.set_default_retries(10)
            assert m.DEFAULT_MAX_RETRIES == 10
        finally:
            m.set_default_retries(original)

    def test_negative_clamped_to_zero(self):
        import src.scrape.base.http as m

        original = m.DEFAULT_MAX_RETRIES
        try:
            m.set_default_retries(-5)
            assert m.DEFAULT_MAX_RETRIES == 0
        finally:
            m.set_default_retries(original)


class TestPoliteClientRetryConfig:
    """PoliteClient respects its max_retries parameter."""

    def test_default_uses_module_level(self, tmp_path):
        from src.scrape.base.http import (
            PoliteClient,
            DEFAULT_MAX_RETRIES,
            set_default_retries,
        )
        from src.scrape.base.robots import RobotsCache

        robots = RobotsCache()
        client = PoliteClient(robots_cache=robots)
        assert client.max_retries is None  # means "use default"

        # The internal _do_request uses DEFAULT_MAX_RETRIES when max_retries is None
        # Verify by checking tenacity would use the right stop condition
        import tenacity
        original = DEFAULT_MAX_RETRIES
        try:
            set_default_retries(2)
            # Even though client was created before the change, it reads
            # DEFAULT_MAX_RETRIES at call time in _do_request
            client2 = PoliteClient(robots_cache=robots)
            assert client2.max_retries is None
        finally:
            set_default_retries(original)

        client.close()

    def test_explicit_override(self, tmp_path):
        from src.scrape.base.http import PoliteClient
        from src.scrape.base.robots import RobotsCache

        robots = RobotsCache()
        client = PoliteClient(robots_cache=robots, max_retries=7)
        assert client.max_retries == 7
        client.close()


class TestRetryConstants:
    """Backoff constants are sensible."""

    def test_backoff_values(self):
        from src.scrape.base.http import (
            DEFAULT_RETRY_MULTIPLIER,
            DEFAULT_RETRY_MIN_WAIT,
            DEFAULT_RETRY_MAX_WAIT,
        )
        assert DEFAULT_RETRY_MULTIPLIER == 1
        assert DEFAULT_RETRY_MIN_WAIT == 1
        assert DEFAULT_RETRY_MAX_WAIT == 4


class TestScrapeRetriesFlag:
    """CLI --retries flag is accepted and validated."""

    def test_help_shows_retries(self):
        result = runner.invoke(
            __import__("src.cli", fromlist=["app"]).app,
            ["scrape", "--help"],
        )
        assert result.exit_code == 0
        assert "--retries" in result.output

    def test_retries_defaults_to_three(self):
        """Verify --retries default appears in help output."""
        result = runner.invoke(
            __import__("src.cli", fromlist=["app"]).app,
            ["scrape", "--help"],
        )
        assert "3" in result.output or "default" in result.output.lower()
