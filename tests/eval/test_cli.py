"""Smoke tests for the ``mkt eval`` CLI command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.cli import app


@pytest.fixture(autouse=True)
def _hash_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HASH_SALT", "test-eval-cli-salt")


def test_eval_mock_prints_summary_and_exits_zero() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "--provider", "mock"])
    assert result.exit_code == 0, result.output
    assert "fixtures" in result.output
    assert "mean" in result.output


def test_eval_json_emits_machine_readable_output() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "--provider", "mock", "--json"])
    assert result.exit_code == 0, result.output
    # The output may contain log lines too — find the JSON body by
    # locating the first '{'.
    body = result.output[result.output.index("{"):]
    data = json.loads(body)
    assert "scores" in data
    assert "mean_recovery_rate" in data
    assert len(data["scores"]) == 5


def test_eval_min_recovery_threshold_triggers_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fixture where the persona recovers nothing must fail --min-recovery."""
    from src.eval.runner import EVAL_DIR, load_fixture

    fixture = load_fixture(EVAL_DIR / "whatsapp_hk.json")
    # Replace pain points with off-topic claims so no theme is recovered.
    # Evidence + quotes must still resolve to real docs so the validator
    # passes on the first try (the mock queue has no spare retries).
    for cid, persona in fixture["mock_persona_responses"].items():
        cluster = next(c for c in fixture["clusters"]
                       if c["cluster_id"] == cid)
        first_pid = cluster["post_ids"][0]
        persona["pain_points"] = [{
            "claim": "completely unrelated finding",
            "severity": "low",
            "evidence": [f"<{first_pid}>"],
        }]

    target = tmp_path / "products"
    target.mkdir()
    (target / "whatsapp_hk.json").write_text(
        json.dumps(fixture), encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["eval", "--provider", "mock",
              "--directory", str(target),
              "--min-recovery", "0.5"],
    )
    assert result.exit_code == 2, result.output


def test_eval_fails_clearly_when_fixture_directory_is_empty(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["eval", "--provider", "mock",
              "--directory", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "No eval fixtures" in (result.output or result.stderr or "")
