"""CSV export for raw posts and persona summaries.

CLI: ``mkt export --format csv --topic X --region Y``

Produces:
  - ``data/exports/{topic_slug}/{region}/raw_posts.csv``
  - ``data/exports/{topic_slug}/{region}/personas.csv``
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Annotated

import typer

from src.schemas.raw import RawPost
from src.schemas.synthesis import Persona

_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "data"


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "untitled"


_export_app = typer.Typer(help="Export data to CSV", no_args_is_help=True)


def _raw_posts_csv(topic_slug: str, region: str, out_path: Path) -> int:
    """Export all raw posts for a topic/region to CSV. Returns row count."""
    raw_dir = _DATA_DIR / "raw" / topic_slug / region
    if not raw_dir.exists():
        typer.echo(f"No raw data at {raw_dir}", err=True)
        return 0

    json_files = sorted(raw_dir.glob("*.json"))
    run_files = [f for f in json_files if not f.name.endswith("._run.json")]
    if not run_files:
        typer.echo(f"No run data at {raw_dir}", err=True)
        return 0

    # Collect all raw posts
    all_posts: list[dict] = []
    for rf in run_files:
        with open(rf, encoding="utf-8") as f:
            posts_data = json.load(f)
        all_posts.extend(posts_data)

    if not all_posts:
        typer.echo("No posts found.")
        return 0

    # CSV columns — flat, user-friendly field names
    fieldnames = [
        "id",
        "source",
        "source_category",
        "region",
        "language",
        "language_detected",
        "url",
        "author_hash",
        "title",
        "body",
        "posted_at",
        "signal_type",
        "engagement_metrics",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for p in all_posts:
            # Flatten engagement_metrics to a JSON string for CSV compatibility
            row = dict(p)
            if "engagement_metrics" in row and row["engagement_metrics"]:
                row["engagement_metrics"] = json.dumps(row["engagement_metrics"], ensure_ascii=False)
            else:
                row["engagement_metrics"] = ""
            writer.writerow(row)

    return len(all_posts)


def _personas_csv(topic_slug: str, region: str, out_path: Path) -> int:
    """Export persona summaries to CSV. Returns row count."""
    personas_dir = _DATA_DIR / "personas" / topic_slug / region
    if not personas_dir.exists():
        typer.echo(f"No persona data at {personas_dir}", err=True)
        return 0

    json_files = sorted(personas_dir.glob("*.json"))
    if not json_files:
        typer.echo(f"No persona files at {personas_dir}", err=True)
        return 0

    fieldnames = [
        "id",
        "run_id",
        "cluster_id",
        "name",
        "one_liner",
        "language",
        "cluster_size",
        "confidence",
        "generated_at",
        "model",
        "provider",
        "goals",
        "motivations",
        "pain_points",
        "preferred_channels",
        "behaviors",
        "data_source_coverage",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for pf in json_files:
            with open(pf, encoding="utf-8") as fh:
                data = json.load(fh)

            # Flatten complex fields for CSV readability
            row: dict = dict(data)

            for claim_field in ("goals", "motivations", "pain_points", "preferred_channels", "behaviors"):
                if claim_field in row and isinstance(row[claim_field], dict):
                    claims = row[claim_field].get("claims", [])
                    summaries = [c.get("claim", "") for c in claims if isinstance(c, dict)]
                    row[claim_field] = " | ".join(summaries)

            if "data_source_coverage" in row and row["data_source_coverage"]:
                row["data_source_coverage"] = json.dumps(row["data_source_coverage"], ensure_ascii=False)
            else:
                row["data_source_coverage"] = ""

            writer.writerow(row)
            rows_written += 1

    return rows_written


@_export_app.command(name="csv")
def export_csv(
    topic: Annotated[str, typer.Option(..., "--topic", help="Topic slug or search term.")],
    region: Annotated[str, typer.Option(..., "--region", help="Canonical region code, e.g. HK.")],
) -> None:
    """Export raw posts and persona summaries as CSV."""
    topic_slug = _slugify(topic)
    exports_dir = _DATA_DIR / "exports" / topic_slug / region
    exports_dir.mkdir(parents=True, exist_ok=True)

    # Raw posts
    posts_path = exports_dir / "raw_posts.csv"
    post_count = _raw_posts_csv(topic_slug, region, posts_path)
    if post_count:
        typer.echo(f"  raw_posts: {post_count} rows -> {posts_path}")
    else:
        typer.echo("  raw_posts: (no data)")

    # Personas
    persona_path = exports_dir / "personas.csv"
    persona_count = _personas_csv(topic_slug, region, persona_path)
    if persona_count:
        typer.echo(f"  personas:  {persona_count} rows -> {persona_path}")
    else:
        typer.echo("  personas:  (no data)")

    typer.echo(f"\nDone. Files in {exports_dir}")
