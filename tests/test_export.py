"""CSV export tests — raw posts and persona CSV output."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.cli import app as _cli_app
from src.schemas.raw import RawPost
from src.schemas.synthesis import Persona, ClaimList, EvidenceClaim


runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populate_raw_posts(data_dir: Path, topic_slug: str, region: str) -> list[dict]:
    """Create a minimal raw post JSON file and return its records."""
    raw_dir = data_dir / "raw" / topic_slug / region
    raw_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    posts = [
        {
            "id": "post_001",
            "source": "lihkg",
            "source_category": "FORUMS",
            "region": region,
            "language": "zh",
            "language_detected": "zh",
            "url": "https://example.com/1",
            "author_hash": "abc123",
            "title": "Test title 1",
            "body": "This is a test post about market research.",
            "posted_at": now,
            "signal_type": "OPINION",
            "engagement_metrics": {"upvotes": 5, "replies": 3},
        },
        {
            "id": "post_002",
            "source": "reddit_old",
            "source_category": "FORUMS",
            "region": region,
            "language": "en",
            "language_detected": "en",
            "url": "https://example.com/2",
            "author_hash": "def456",
            "title": "Test title 2",
            "body": "Another post with different content.",
            "posted_at": now,
            "signal_type": "OPINION",
            "engagement_metrics": {},
        },
    ]

    out_path = raw_dir / "run_20250101.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False)

    return posts


def _populate_personas(data_dir: Path, topic_slug: str, region: str) -> list[dict]:
    """Create minimal persona JSON files and return their records."""
    personas_dir = data_dir / "personas" / topic_slug / region
    personas_dir.mkdir(parents=True, exist_ok=True)

    personas = [
        {
            "id": "persona_01",
            "run_id": "run_001",
            "cluster_id": "cluster_01",
            "name": "Price-Sensitive Parents",
            "one_liner": "Parents who track prices carefully.",
            "language": "en",
            "cluster_size": 25,
            "confidence": 0.85,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": "claude-sonnet-4-20250514",
            "provider": "anthropic",
            "goals": {
                "claims": [
                    {"claim": "Find affordable options", "evidence": ["post_001"]},
                    {"claim": "Compare prices across stores", "evidence": ["post_002"]},
                ],
                "coverage": "ok",
            },
            "motivations": {
                "claims": [
                    {"claim": "Save money for family", "evidence": ["post_001"]},
                ],
                "coverage": "ok",
            },
            "pain_points": {"claims": [], "coverage": "unverified"},
            "preferred_channels": {"claims": [], "coverage": "ok"},
            "behaviors": {"claims": [], "coverage": "ok"},
            "data_source_coverage": {"lihkg": 15, "reddit_old": 10},
        },
        {
            "id": "persona_02",
            "run_id": "run_001",
            "cluster_id": "cluster_02",
            "name": "Early Adopters",
            "one_liner": "Tech-savvy users who try new products first.",
            "language": "en",
            "cluster_size": 18,
            "confidence": 0.72,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": "claude-sonnet-4-20250514",
            "provider": "anthropic",
            "goals": {"claims": [], "coverage": "ok"},
            "motivations": {"claims": [], "coverage": "ok"},
            "pain_points": {"claims": [], "coverage": "ok"},
            "preferred_channels": {"claims": [], "coverage": "ok"},
            "behaviors": {"claims": [], "coverage": "ok"},
            "data_source_coverage": {"reddit_old": 18},
        },
    ]

    for p in personas:
        out_path = personas_dir / f"{p['id']}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False)

    return personas


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_export_raw_posts_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """CSV export of raw posts produces valid CSV with correct columns."""
    from src.cli import _DATA_DIR as _REAL_DATA_DIR

    data_dir = tmp_path / "data"
    topic_slug = "test_topic"
    region = "HK"

    monkeypatch.setattr("src.cli._DATA_DIR", data_dir)
    monkeypatch.setattr("src.cli_export._DATA_DIR", data_dir)

    posts = _populate_raw_posts(data_dir, topic_slug, region)

    result = runner.invoke(
        _cli_app,
        ["export", "csv", "--topic", "test_topic", "--region", region],
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exc_info}"

    exports_dir = data_dir / "exports" / topic_slug / region
    csv_path = exports_dir / "raw_posts.csv"
    assert csv_path.exists(), f"CSV not found at {csv_path}"

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    assert rows[0]["id"] == "post_001"
    assert rows[0]["source"] == "lihkg"
    assert rows[0]["title"] == "Test title 1"

    # engagement_metrics should be serialized as JSON string
    metrics = json.loads(rows[0]["engagement_metrics"])
    assert metrics["upvotes"] == 5


def test_export_personas_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """CSV export of personas produces valid CSV with flattened claim fields."""
    data_dir = tmp_path / "data"
    topic_slug = "test_topic"
    region = "HK"

    monkeypatch.setattr("src.cli._DATA_DIR", data_dir)
    monkeypatch.setattr("src.cli_export._DATA_DIR", data_dir)

    personas = _populate_personas(data_dir, topic_slug, region)

    result = runner.invoke(
        _cli_app,
        ["export", "csv", "--topic", "test_topic", "--region", region],
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exc_info}"

    exports_dir = data_dir / "exports" / topic_slug / region
    csv_path = exports_dir / "personas.csv"
    assert csv_path.exists(), f"CSV not found at {csv_path}"

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

    p1_row = next(r for r in rows if r["id"] == "persona_01")
    assert p1_row["name"] == "Price-Sensitive Parents"
    assert p1_row["cluster_size"] == "25"

    # Goals should be pipe-joined claims
    assert "Find affordable options" in p1_row["goals"]
    assert "Compare prices across stores" in p1_row["goals"]
    assert " | " in p1_row["goals"]

    # data_source_coverage should be JSON
    coverage = json.loads(p1_row["data_source_coverage"])
    assert coverage["lihkg"] == 15


def test_export_empty_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Export with no data should succeed with a helpful message."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr("src.cli._DATA_DIR", data_dir)
    monkeypatch.setattr("src.cli_export._DATA_DIR", data_dir)

    result = runner.invoke(
        _cli_app,
        ["export", "csv", "--topic", "no_data", "--region", "HK"],
    )
    assert result.exit_code == 0
    assert "(no data)" in result.output


def test_export_help():
    """mkt export csv --help should show usage."""
    result = runner.invoke(_cli_app, ["export", "csv", "--help"])
    assert result.exit_code == 0
    assert "--topic" in result.output
    assert "--region" in result.output
