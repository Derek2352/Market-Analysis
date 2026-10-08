"""Phase 6 CLI tests: --accept-tos-risk flag + opt-in warning emission."""
from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from src import cli
from src.cli import app


class _NoopScraper:
    def search(self, topic, *, since, limit):
        return iter(())


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """These tests are about warnings, not scraping: never touch the network
    (openrice's ToS prohibits scraping) and never write into the real data/."""
    monkeypatch.setenv("AUTHOR_HASH_SALT", "t")
    monkeypatch.setattr(cli, "get_scraper", lambda source_id, **kw: _NoopScraper())
    monkeypatch.setattr(cli, "_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cli, "_ROOT", tmp_path)
    monkeypatch.setattr("src.scrape.utils.egress.check_egress", lambda *a, **k: ("ok", "stub"))


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

    The scraper is stubbed — we only assert the warning text appears.
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
