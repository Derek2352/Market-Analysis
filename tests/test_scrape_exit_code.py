"""`mkt scrape` exit codes: total failure is non-zero, partial success is 0."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from src import cli
from src.schemas.raw import RawPost


def _post(i: int) -> RawPost:
    return RawPost(
        id=f"ok_{i}", source="good", source_category="reviews", region="HK",
        language="en", url=f"https://example.com/{i}", author_hash="0" * 64,
        title="t", body="b", posted_at=datetime.now(timezone.utc), signal_type="opinion",
    )


class _Good:
    def search(self, q, *, since, limit):
        yield from (_post(i) for i in range(3))


class _Boom:
    def search(self, q, *, since, limit):
        raise RuntimeError("HTTP 403 — blocked")
        yield  # pragma: no cover


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHOR_HASH_SALT", "t")
    monkeypatch.setattr(cli, "_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cli, "_ROOT", tmp_path)
    monkeypatch.setattr(cli, "available_sources", lambda: ["good", "bad1", "bad2", "ctor"])
    def get_scraper(sid, **kw):
        if sid == "good":
            return _Good()
        if sid == "ctor":
            raise ValueError("cannot construct")
        return _Boom()
    monkeypatch.setattr(cli, "get_scraper", get_scraper)
    monkeypatch.setattr("src.scrape.utils.egress.check_egress",
                        lambda *a, **k: ("policy", "proxy refused CONNECT"))
    return CliRunner()


def _run(runner, sources):
    return runner.invoke(cli.app, ["scrape", "--topic", "x", "--region", "HK",
                                   "--sources", sources, "--no-progress", "--accept-tos-risk"])


def test_all_sources_failing_exits_3_and_names_cause(cli_env):
    r = _run(cli_env, "bad1,bad2")
    assert r.exit_code == 3, r.output
    assert "All 2 source(s) failed" in r.output
    assert "policy" in r.output


def test_partial_success_exits_0(cli_env):
    r = _run(cli_env, "good,bad1")
    assert r.exit_code == 0, r.output


def test_constructor_error_is_isolated(cli_env):
    r = _run(cli_env, "ctor,good")
    assert r.exit_code == 0, r.output
    assert "cannot construct" in r.output
