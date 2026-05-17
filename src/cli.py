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
    from src.pipeline.synthesize import generate_persona, generate_journey
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
    subreddits: Annotated[
        str,
        typer.Option("--subreddits", help="Comma-separated subreddits (for reddit_old source)."),
    ] = "",
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
            # Pass subreddits for reddit_old scraper
            scraper_kwargs = {}
            if source_id == "reddit_old" and subreddits:
                scraper_kwargs["subreddits"] = [s.strip() for s in subreddits.split(",") if s.strip()]
            scraper = get_scraper(source_id, **scraper_kwargs)
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

    # Load post texts for c-TF-IDF keywords
    topic_slug = _slugify(topic)
    raw_dir = _DATA_DIR / "raw" / topic_slug / region
    post_texts: dict[str, str] = {}
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf) as f:
            raw_posts = json.load(f)
        for p in raw_posts:
            pid = p.get("id", "")
            text = f"{p.get('title', '') or ''}\n{p.get('body', '') or ''}"
            if pid and pid in set(post_ids):
                post_texts[pid] = text

    result = cluster_embeddings(
        vectors, post_ids, topic, region,
        config=cfg,
        source_map=source_map,
        post_texts=post_texts if post_texts else None,
    )

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

    typer.echo("Generating diagnostics report...")
    out_path = clusters_dir / "diagnostics.md"
    report = generate_report(result, post_texts, out_path, params=result.params)
    typer.echo(f"Report saved: {out_path}")
    typer.echo(report)


@app.command()
def persona(
    topic: Annotated[str, typer.Option(..., "--topic")],
    region: Annotated[str, typer.Option(..., "--region")],
    cluster_id: Annotated[
        str,
        typer.Option("--cluster", help="Specific cluster ID. Omit to generate for all clusters."),
    ] = "",
) -> None:
    """Generate personas from clustered posts via Claude."""
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

    # Load post texts and metadata
    raw_dir = _DATA_DIR / "raw" / topic_slug / region
    post_texts: dict[str, str] = {}
    post_metadata: dict[str, dict] = {}
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf) as f:
            posts = json.load(f)
        for p in posts:
            pid = p.get("id", "")
            if not pid:
                continue
            post_texts[pid] = f"{p.get('title', '') or ''}\n{p.get('body', '') or ''}"
            post_metadata[pid] = {
                "source": p.get("source", ""),
                "url": p.get("url", ""),
                "lang": p.get("language_detected", "en"),
            }

    targets = [c for c in result.clusters if not cluster_id or c.cluster_id == cluster_id]
    if not targets:
        typer.echo(f"Cluster {cluster_id} not found. Available: {[c.cluster_id for c in result.clusters]}")
        raise typer.Exit(code=1)

    personas_out = _DATA_DIR / "personas" / topic_slug / region
    personas_out.mkdir(parents=True, exist_ok=True)

    for c in targets:
        typer.echo(f"Generating persona for {c.cluster_id} ({c.size} posts)...")
        try:
            p = generate_persona(c, post_texts, post_metadata)
            out_path = personas_out / f"{p.id}.json"
            with open(out_path, "w") as f:
                json.dump(p.model_dump(mode="json"), f, indent=2, default=str)
            typer.echo(f"  {p.name}: {p.one_liner}")
            typer.echo(f"  Saved: {out_path}")
        except Exception as e:
            typer.echo(f"  Failed: {e}")


@app.command()
def journey(
    topic: Annotated[str, typer.Option(..., "--topic")],
    region: Annotated[str, typer.Option(..., "--region")],
    persona_id: Annotated[str, typer.Option(..., "--persona", help="Persona ID from mkt persona output.")],
) -> None:
    """Generate a user journey map for a persona via Claude."""
    if not _PIPELINE_AVAILABLE:
        typer.echo("Pipeline deps not installed.", err=True)
        raise typer.Exit(code=1)

    topic_slug = _slugify(topic)
    personas_dir = _DATA_DIR / "personas" / topic_slug / region

    persona_path = personas_dir / f"{persona_id}.json"
    if not persona_path.exists():
        typer.echo(f"Persona not found: {persona_path}", err=True)
        raise typer.Exit(code=1)

    import json
    with open(persona_path) as f:
        pdata = json.load(f)

    from src.schemas.synthesis import Persona as PersonaModel
    persona_obj = PersonaModel(**pdata)

    # Load cluster
    clusters_dir = _DATA_DIR / "clusters" / topic_slug / region
    cluster_files = sorted(clusters_dir.glob("clusters_*.json"))
    with open(cluster_files[-1]) as f:
        cdata = json.load(f)

    cluster = None
    for c in cdata.get("clusters", []):
        if c.get("cluster_id") == persona_obj.cluster_id:
            cluster = Cluster(**c)
            break

    if not cluster:
        typer.echo(f"Cluster {persona_obj.cluster_id} not found.", err=True)
        raise typer.Exit(code=1)

    # Load text/metadata
    raw_dir = _DATA_DIR / "raw" / topic_slug / region
    post_texts: dict[str, str] = {}
    post_metadata: dict[str, dict] = {}
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf) as f:
            posts = json.load(f)
        for p in posts:
            pid = p.get("id", "")
            if not pid:
                continue
            post_texts[pid] = f"{p.get('title', '') or ''}\n{p.get('body', '') or ''}"
            post_metadata[pid] = {
                "source": p.get("source", ""),
                "url": p.get("url", ""),
                "lang": p.get("language_detected", "en"),
            }

    typer.echo(f"Generating journey map for {persona_obj.name}...")
    try:
        jm = generate_journey(persona_obj, cluster, post_texts, post_metadata)
        journeys_out = _DATA_DIR / "journeys" / topic_slug / region
        journeys_out.mkdir(parents=True, exist_ok=True)
        out_path = journeys_out / f"{jm.id}.json"
        with open(out_path, "w") as f:
            json.dump(jm.model_dump(mode="json"), f, indent=2, default=str)
        typer.echo(f"Journey map saved: {out_path}")
        for s in jm.stages:
            emoji = {"Awareness": "👁", "Consideration": "🤔", "Decision": "✅",
                     "Onboarding": "🚀", "Use": "🔄", "Loyalty/Churn": "💚"}.get(s.stage, "")
            typer.echo(f"  {emoji} {s.stage}: {len(s.touchpoints)} touchpoints, {len(s.frictions)} frictions ({s.coverage})")
    except Exception as e:
        typer.echo(f"Failed: {e}")


if __name__ == "__main__":
    app()
