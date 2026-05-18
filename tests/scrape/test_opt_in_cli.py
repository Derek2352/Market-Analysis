"""Phase 6 CLI tests: --accept-tos-risk flag + opt-in warning emission."""
from __future__ import annotations

import re

from typer.testing import CliRunner

from src.cli import app


def _runner() -> CliRunner:
    return CliRunner()


def test_unknown_source_aborts_with_help_message() -> None:
    r = _runner().invoke(
        app, ["scrape", "--topic", "x", "--region", "HK",
              "--sources", "definitely_not_a_real_source", "--since", "1d"],
    )
    assert r.exit_code == 2
    assert "Available sources" in r.output


def test_warning_emitted_when_prohibited_source_enabled() -> None:
    """openrice is opt-in (prohibited). Listing it should produce a warning
    on stderr unless --accept-tos-risk is passed.

    The scraper itself will fail (network blocked) — we only assert the
    warning text appears before the scrape attempt.
    """
    r = _runner().invoke(
        app,
        ["scrape", "--topic", "x", "--region", "HK",
         "--sources", "openrice", "--since", "1d", "--limit", "1"],
        catch_exceptions=True,
    )
    out = (r.stderr or "") + (r.output or "")
    assert re.search(r"⚠.*openrice.*prohibited.*ToS", out)


def test_accept_tos_risk_suppresses_warning() -> None:
    r = _runner().invoke(
        app,
        ["scrape", "--topic", "x", "--region", "HK",
         "--sources", "openrice", "--since", "1d", "--limit", "1",
         "--accept-tos-risk"],
        catch_exceptions=True,
    )
    out = (r.stderr or "") + (r.output or "")
    assert "prohibited by its ToS" not in out


def test_default_source_list_never_warns() -> None:
    """When --sources is omitted, only default_enabled=True sources run;
    no opt-in warning should be emitted regardless of the flag."""
    r = _runner().invoke(
        app,
        ["scrape", "--topic", "x", "--region", "HK",
         "--since", "1d", "--limit", "1"],
        catch_exceptions=True,
    )
    out = (r.stderr or "") + (r.output or "")
    assert "prohibited by its ToS" not in out
