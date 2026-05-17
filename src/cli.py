from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import typer

from src.regions.registry import get_region
from src.scrape.registry import available_sources, get_scraper
from src.scrape.utils.dedup import DedupIndex
from src.scrape.utils.logging import configure_logging
from src.scrape.utils.since import parse_since
from src.scrape.utils.writer import RunWriter
from src.schemas.cluster import ClusteringResult
from src.schemas.raw import RawPost
from src.scrape.doctor import app as doctor_app

# Pipeline typeshed
_PIPELINE_AVAILABLE = True
try:
    from src.pipeline.embed import EmbeddingStore
    from src.pipeline.cluster import cluster_embeddings, load_config
    from src.pipeline.cluster_diag import compute_ctfidf_keywords, generate_report
except ImportError:
    _PIPELINE_AVAILABLE = False

app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(doctor_app, name="scrape-doctor")


@app.callback()
def _main() -> None:
    """Market analytics CLI."""


_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "data"
_LOG_DIR = _ROOT / "logs"


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "untitled"


@app.command()
def scrape(
    topic: Annotated[str, typer.Option(..., "--topic", help="Search term or numeric app id.")],
    region: Annotated[str, typer.Option(..., "--region", help="Canonical region code, e.g. HK.")],
    sources: Annotated[
        str,
        typer.Option(
            "--sources",
            help="Comma-separated source ids. Omit to use the region default list.",
        ),
    ] = "",
    limit: Annotated[
        int, typer.Option("--limit", min=1, help="Max records per source.")
    ] = 500,
    since: Annotated[
        str, typer.Option("--since", help="Relative window, e.g. 90d, 24h, 2w, 6m.")
    ] = "90d",
) -> None:
    """Scrape one or more sources for `topic` in `region`."""
    region_cfg = get_region(region)

    if sources:
        source_ids = [s.strip() for s in sources.split(",") if s.strip()]
    else:
        source_ids = region_cfg.default_source_ids()

    unknown = [s for s in source_ids if s not in available_sources()]
    if unknown:
        typer.echo(
            f"Available sources: {available_sources()}. "
            f"Not implemented: {unknown}. ",
            err=True,
        )
        raise typer.Exit(code=2)

    since_dt = parse_since(since)
    topic_slug = _slugify(topic)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = configure_logging(_LOG_DIR, run_id).bind(
        topic=topic, topic_slug=topic_slug, region=region
    )

    log.info(
        "scrape.start",
        sources=source_ids,
        limit=limit,
        since=since_dt.isoformat(),
    )

    with DedupIndex(_DATA_DIR / "dedup.sqlite") as index:
        for source_id in source_ids:
            writer = RunWriter(
                data_dir=_DATA_DIR,
                topic_slug=topic_slug,
                region=region,
                source=source_id,
                run_id=run_id,
            )
            scraper = get_scraper(source_id)
            emitted = 0
            duplicates = 0
            try:
                for post in scraper.search(topic, since=since_dt, limit=limit):
                    is_new = index.mark_seen(
                        source=source_id,
                        source_post_id=post.id,
                        region=region,
                        topic_slug=topic_slug,
                    )
                    if is_new:
                        writer.add(post)
                        emitted += 1
                    else:
                        duplicates += 1
            finally:
                close = getattr(scraper, "close", None)
                if callable(close):
                    close()

            extra_meta: dict[str, Any] = {"duplicates_skipped": duplicates}
            cap_apps = getattr(scraper, "cap_hit_apps", [])
            extra_meta["cap_hit"] = bool(cap_apps)
            if cap_apps:
                extra_meta["cap_hit_apps"] = list(cap_apps)

            out_path = writer.finalize(**extra_meta)
            log.info(
                "scrape.source.done",
                source=source_id,
                emitted=emitted,
                duplicates=duplicates,
                output=str(out_path.relative_to(_ROOT)),
                cap_hit=extra_meta["cap_hit"],
            )

    log.info("scrape.done")


# ---------------------------------------------------------------------------
# Phase 3: Pipeline commands
# ---------------------------------------------------------------------------

@app.command()
def embed(
    topic: Annotated[str, typer.Option(..., "--topic")],
    region: Annotated[str, typer.Option(..., "--region")],
) -> None:
    """Embed scraped posts using BGE-M3, store in DuckDB."""
    if not _PIPELINE_AVAILABLE:
        typer.echo("Pipeline deps not installed. Run: pip install sentence-transformers duckdb", err=True)
        raise typer.Exit(code=1)

    topic_slug = _slugify(topic)
    raw_dir = _DATA_DIR / "raw" / topic_slug / region
    db_path = _DATA_DIR / "embeddings.duckdb"

    if not raw_dir.exists():
        typer.echo(f"No scraped data at {raw_dir}. Run scrape first.", err=True)
        raise typer.Exit(code=1)

    json_files = sorted(raw_dir.glob("*.json"))
    run_files = [f for f in json_files if not f.name.endswith("._run.json")]
    if not run_files:
        typer.echo(f"No run data at {raw_dir}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Found {len(run_files)} run files")
    store = EmbeddingStore(db_path=db_path)

    import json
    total = 0
    for rf in run_files:
        with open(rf) as f:
            posts_data = json.load(f)
        posts = [RawPost(**p) for p in posts_data]
        n = store.embed_posts(posts, topic=topic, region=region)
        total += n
        typer.echo(f"  {rf.name}: {n} new embeddings")

    stats = store.get_stats()
    store.close()
    typer.echo(f"\nDone. {total} new embeddings. Store: {stats['total_embeddings']} total.")


@app.command()
def cluster(
    topic: Annotated[str, typer.Option(..., "--topic")],
    region: Annotated[str, typer.Option(..., "--region")],
    config_path: Annotated[
        str,
        typer.Option("--config", help="Path to clustering.yaml"),
    ] = "configs/clustering.yaml",
) -> None:
    """Cluster embeddings with UMAP + HDBSCAN."""
    if not _PIPELINE_AVAILABLE:
        typer.echo("Pipeline deps not installed.", err=True)
        raise typer.Exit(code=1)

    db_path = _DATA_DIR / "embeddings.duckdb"
    if not db_path.exists():
        typer.echo(f"No embeddings at {db_path}. Run embed first.", err=True)
        raise typer.Exit(code=1)

    import duckdb
    import numpy as np

    cfg = load_config(Path(config_path) if config_path else None)

    con = duckdb.connect(str(db_path))
    con.execute("LOAD vss;")

    rows = con.execute(
        "SELECT post_id, source, vector, topic FROM embeddings WHERE topic = ? AND region = ?",
        [topic, region],
    ).fetchall()

    if not rows:
        typer.echo(f"No embeddings for topic={topic}, region={region}")
        con.close()
        raise typer.Exit(code=1)

    vectors = np.array([np.array(r[2]) for r in rows])
    post_ids = [r[0] for r in rows]
    sources = [r[1] for r in rows]

    typer.echo(f"Clustering {len(rows)} embeddings for {topic} ({region})")

    source_map = dict(zip(post_ids, sources))
    result = cluster_embeddings(
        vectors, post_ids, topic, region,
        config=cfg,
        source_map=source_map,
    )

    topic_slug = _slugify(topic)
    out_dir = _DATA_DIR / "clusters" / topic_slug / region
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"clusters_{ts}.json"

    import json
    with open(out_path, "w") as f:
        json.dump(result.model_dump(mode="json"), f, indent=2, default=str)

    typer.echo(f"Clusters: {len(result.clusters)}, Noise: {result.noise_count}")
    typer.echo(f"Saved: {out_path}")
    con.close()


@app.command()
def diag(
    topic: Annotated[str, typer.Option(..., "--topic")],
    region: Annotated[str, typer.Option(..., "--region")],
) -> None:
    """Generate cluster quality diagnostics report."""
    if not _PIPELINE_AVAILABLE:
        typer.echo("Pipeline deps not installed.", err=True)
        raise typer.Exit(code=1)

    topic_slug = _slugify(topic)
    clusters_dir = _DATA_DIR / "clusters" / topic_slug / region

    if not clusters_dir.exists():
        typer.echo(f"No clusters at {clusters_dir}. Run cluster first.", err=True)
        raise typer.Exit(code=1)

    cluster_files = sorted(clusters_dir.glob("clusters_*.json"))
    if not cluster_files:
        typer.echo(f"No cluster results at {clusters_dir}", err=True)
        raise typer.Exit(code=1)

    import json

    latest = cluster_files[-1]
    with open(latest) as f:
        data = json.load(f)

    result = ClusteringResult(**data)

    raw_dir = _DATA_DIR / "raw" / topic_slug / region
    post_texts: dict[str, str] = {}
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf) as f:
            posts = json.load(f)
        for p in posts:
            pid = p.get("id", "")
            text = f"{p.get('title', '') or ''}\n{p.get('body', '') or ''}"
            if pid:
                post_texts[pid] = text

    typer.echo("Computing c-TF-IDF keywords...")
    keywords = compute_ctfidf_keywords(result.clusters, post_texts)
    for c in result.clusters:
        c.keyword_summary = keywords.get(c.cluster_id, [])

    out_path = clusters_dir / "diagnostics.md"
    report = generate_report(result, post_texts, out_path, params=result.params)
    typer.echo(f"Report saved: {out_path}")
    typer.echo(report)


if __name__ == "__main__":
    app()
