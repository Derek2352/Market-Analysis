from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv

load_dotenv()

from src.config import get_default_region as _get_default_region
from src.regions.registry import get_region
from src.scrape.registry import available_sources, get_scraper
from src.scrape.utils.dedup import DedupIndex
from src.scrape.utils.logging import configure_logging
from src.scrape.utils.since import parse_since
from src.scrape.utils.writer import RunWriter
from src.schemas.cluster import ClusteringResult
from src.schemas.raw import RawPost
from src.scrape.doctor import app as doctor_app
from src.cli_export import _export_app
from src.cli_doctor import doctor as _doctor_cmd
from src.cli_eval import eval_cmd as _eval_cmd
from src.cli_render import render_app as _render_app

# Pipeline typeshed
_PIPELINE_AVAILABLE = True
try:
    from src.pipeline.embed import EmbeddingStore
    from src.pipeline.cluster import cluster_embeddings, load_config
    from src.pipeline.cluster_diag import compute_ctfidf_keywords, generate_report
    from src.pipeline.synthesize import (
        CostCapExceeded,
        MissingAPIKey,
        SynthesisError,
        synthesize_run,
        synthesize_temporal,
        synthesize_comparative,
    )
except ImportError:
    _PIPELINE_AVAILABLE = False

app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(doctor_app, name="scrape-doctor")
app.add_typer(_export_app, name="export")
app.command(name="doctor")(_doctor_cmd)
app.command(name="eval")(_eval_cmd)
app.add_typer(_render_app, name="render")


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
    retries: Annotated[
        int,
        typer.Option(
            "--retries",
            min=0,
            max=10,
            help="Max retry attempts per HTTP request on transient failures (429/5xx). Default 3.",
        ),
    ] = 3,
    no_progress: Annotated[
        bool,
        typer.Option(
            "--no-progress",
            help="Disable tqdm progress bars (for cron/pipe mode).",
        ),
    ] = False,
    accept_tos_risk: Annotated[
        bool,
        typer.Option(
            "--accept-tos-risk",
            help=(
                "Suppress the per-source ToS-prohibition warning. Sources whose "
                "ToS forbids scraping still run; this flag just hides the "
                "interactive warning text (useful for scripts and CI)."
            ),
        ),
    ] = False,
    bypass_robots: Annotated[
        bool,
        typer.Option(
            "--bypass-robots",
            help="Ignore robots.txt restrictions. Needed for sources like discuss_hk that block scrapers.",
        ),
    ] = False,
    expand: Annotated[
        bool,
        typer.Option(
            "--expand",
            help="Expand the topic into related search queries for broader coverage.",
        ),
    ] = False,
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

    # Enforce opt-in: a source with default_enabled=False must be passed
    # explicitly via --sources. Defaults never include them.
    explicit = bool(sources)
    prohibited: list[tuple[str, str]] = []  # (source_id, last_checked iso)
    for sid in source_ids:
        sc = region_cfg.get_source(sid)
        if sc is None or sc.default_enabled:
            continue
        if not explicit:
            # Should never happen — defaults already filter on default_enabled
            # — but guard anyway in case a caller passes default_source_ids().
            continue
        prohibited.append((sid, str(sc.last_checked) if sc.last_checked else "unknown"))

    if prohibited and not accept_tos_risk:
        for sid, when in prohibited:
            typer.echo(
                f"⚠ {sid} scraping is prohibited by its ToS. You enabled it "
                f"explicitly. ToS last_checked: {when}. Proceed at your own risk.",
                err=True,
            )

    since_dt = parse_since(since)
    topic_slug = _slugify(topic)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = configure_logging(_LOG_DIR, run_id).bind(
        topic=topic, topic_slug=topic_slug, region=region
    )

    # Expand topic into related queries for broader coverage
    if expand:
        from src.scrape.utils.query_expansion import expand_query
        search_queries = expand_query(topic, region, n=6)
        typer.echo(f"Expanded '{topic}' → {len(search_queries)} queries: {search_queries[1:4]}...")
    else:
        search_queries = [topic]

    log.info(
        "scrape.start",
        sources=source_ids,
        limit=limit,
        since=since_dt.isoformat(),
        queries=len(search_queries) if expand else 1,
        retries=retries,
    )

    # Apply retry configuration globally so all PoliteClient instances pick it up
    from src.scrape.base.http import set_default_retries
    set_default_retries(retries)

    with DedupIndex(_DATA_DIR / "dedup.sqlite") as index:
        source_iter = source_ids
        if not no_progress:
            from tqdm import tqdm
            source_iter = tqdm(source_ids, desc="Scraping", unit="source")

        for source_id in source_iter:
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
            if bypass_robots:
                if source_id == "discuss_hk":
                    scraper_kwargs["respect_robots"] = False
            scraper = get_scraper(source_id, **scraper_kwargs)
            emitted = 0
            duplicates = 0
            try:
                # Per-source post-level progress bar (per-page tracking)
                post_iter = scraper.search(search_queries[0], since=since_dt, limit=limit)
                if not no_progress:
                    from tqdm import tqdm
                    post_iter = tqdm(post_iter, total=limit, desc=f"  {source_id}", unit="post", leave=False)
                for query in search_queries:
                    if expand:
                        log.info("scrape.query", source=source_id, query=query)
                    # For expanded queries after the first, re-bind to new scraper search
                    if query != search_queries[0]:
                        post_iter = scraper.search(query, since=since_dt, limit=limit)
                        if not no_progress:
                            from tqdm import tqdm
                            post_iter = tqdm(post_iter, total=limit, desc=f"  {source_id}:{_slugify(query)[:20]}", unit="post", leave=False)
                    for post in post_iter:
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
                        if emitted >= limit:
                            break
                    if emitted >= limit:
                        break
            except Exception as exc:
                log.warning(
                    "scrape.source.error",
                    source=source_id,
                    error=str(exc),
                    emitted=emitted,
                )
                typer.echo(f"  ⚠ {source_id}: {exc}", err=True)
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
# Region commands
# ---------------------------------------------------------------------------

_region_app = typer.Typer(help="Region management", no_args_is_help=True)
app.add_typer(_region_app, name="region")


@_region_app.command(name="list")
def region_list() -> None:
    """List all available regions with source counts."""
    from src.regions.registry import REGIONS  # noqa: F811

    print(f"{'Code':<4} {'Region':<22} {'Sources':>7}  {'Default':>7}")
    print("-" * 44)
    for rid, cfg in sorted(REGIONS.items()):
        defaults = cfg.default_source_ids()
        opt_ins = cfg.opt_in_sources()
        print(
            f"  {rid:<2}  {cfg.display_name:<20}  "
            f"{len(defaults):>4} def  {len(opt_ins):>4} opt-in"
        )


@_region_app.command(name="show")
def region_show(
    region: Annotated[str, typer.Argument(help="Region code (e.g., HK, TW, US, JP)")],
) -> None:
    """Show sources for a region, grouped by category."""
    from src.regions.registry import REGIONS  # noqa: F811

    cfg = get_region(region)
    print(f"\n  {cfg.display_name} ({cfg.region_id})")
    print(f"  Languages: {', '.join(cfg.primary_languages)}")
    print()

    grouped = cfg.by_category(include_opt_in=True)
    for cat, sources_ in grouped.items():
        print(f"  [{cat.value}]")
        for s in sources_:
            status = ""
            if s.excluded_by_constraint:
                status = " [EXCLUDED]"
            elif not s.default_enabled:
                status = " [OPT-IN]"
            print(
                f"    {s.source_id:<24}  p:{s.persona_value} j:{s.journey_value}  "
                f"risk:{s.tos_risk.value:<6}  {s.access_method.value}{status}"
            )
        print()


@_region_app.command(name="set")
def region_set(
    region: Annotated[str, typer.Argument(help="Region code to set as default")],
) -> None:
    """Set the default region for future runs."""
    from src.config import set_default_region  # noqa: F811

    # Validate
    get_region(region)
    set_default_region(region)
    cfg = get_region(region)
    print(f"Default region set to: {cfg.display_name} ({region})")


# ---------------------------------------------------------------------------
# Phase 3: Pipeline commands
# ---------------------------------------------------------------------------

@app.command()
def embed(
    topic: Annotated[str, typer.Option(..., "--topic")],
    region: Annotated[str, typer.Option(..., "--region")],
    no_progress: Annotated[
        bool,
        typer.Option(
            "--no-progress",
            help="Disable tqdm progress bars (for cron/pipe mode).",
        ),
    ] = False,
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
        n = store.embed_posts(posts, topic=topic, region=region, progress=not no_progress)
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
    import json
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
    if len(rows) < 50:
        typer.echo(
            f"  ⚠ Only {len(rows)} posts — need >50 posts for meaningful clustering.\n"
            f"  → Try wider scraping: mkt scrape --topic \"{topic}\" --region {region} --sources app_store_hk,google_play_hk,reddit_old,youtube_html --limit 200 --since 180d",
            err=True,
        )

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

    from src.lang import get_tokenizer as _get_tokenizer
    result = cluster_embeddings(
        vectors, post_ids, topic, region,
        config=cfg,
        source_map=source_map,
        post_texts=post_texts if post_texts else None,
        tokenizer=_get_tokenizer(region),
    )

    out_dir = _DATA_DIR / "clusters" / topic_slug / region
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"clusters_{ts}.json"

    with open(out_path, "w") as f:
        json.dump(result.model_dump(mode="json"), f, indent=2, default=str)

    typer.echo(f"Clusters: {len(result.clusters)}, Noise: {result.noise_count}")
    typer.echo(f"Saved: {out_path}")
    if len(result.clusters) == 0 and len(rows) >= 50:
        typer.echo(
            f"  ⚠ 0 clusters produced — try lowering min_cluster_size in {config_path} or scrape more diverse sources.",
            err=True,
        )
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
def synthesize(
    topic: Annotated[str, typer.Option(..., "--topic")],
    region: Annotated[str, typer.Option(..., "--region")],
    run_id_opt: Annotated[
        str,
        typer.Option(
            "--run",
            help="Specific clustering run timestamp (e.g. 20260517T123000Z). Latest if omitted.",
        ),
    ] = "",
    cluster_id: Annotated[
        str,
        typer.Option("--cluster", help="Specific cluster id. Omit for all clusters in the run."),
    ] = "",
    provider: Annotated[
        str,
        typer.Option(
            "--provider", help="LLM backend. anthropic (default) or deepseek.",
        ),
    ] = "anthropic",
    model: Annotated[
        str,
        typer.Option("--model", help="Override model id (uses provider default if omitted)."),
    ] = "",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print plan + cost estimate; make no API calls."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Bypass the cost cap."),
    ] = False,
    max_cost: Annotated[
        float,
        typer.Option("--max-cost", help="Hard cost ceiling in USD."),
    ] = 4.00,
    temporal: Annotated[
        bool,
        typer.Option(
            "--temporal",
            help="Also compute temporal trend analysis (volume, complaints, spikes).",
        ),
    ] = False,
) -> None:
    """Synthesize a Persona + Journey Map for every cluster of a clustering run.

    Reads clusters from data/clusters/{topic}/{region}/clusters_{run}.json
    and raw posts from data/raw/{topic}/{region}/, calls the LLM with prompt
    caching (evidence pack shared across persona + journey -> ~70% saving on
    the journey call), validates grounding, retries once on failure, then
    marks affected sections coverage=\"unverified\" rather than fabricating.
    """
    if not _PIPELINE_AVAILABLE:
        typer.echo("Pipeline deps not installed.", err=True)
        raise typer.Exit(code=1)

    import json

    topic_slug = _slugify(topic)
    clusters_dir = _DATA_DIR / "clusters" / topic_slug / region
    if not clusters_dir.exists():
        typer.echo(f"No clusters at {clusters_dir}. Run mkt cluster first.", err=True)
        raise typer.Exit(code=1)

    if run_id_opt:
        cluster_file = clusters_dir / f"clusters_{run_id_opt}.json"
        if not cluster_file.exists():
            typer.echo(f"No clustering run at {cluster_file}", err=True)
            raise typer.Exit(code=1)
    else:
        files = sorted(clusters_dir.glob("clusters_*.json"))
        if not files:
            typer.echo(f"No cluster files at {clusters_dir}", err=True)
            raise typer.Exit(code=1)
        cluster_file = files[-1]

    with open(cluster_file) as f:
        result = ClusteringResult(**json.load(f))

    # Run id used in output filenames: take it from the cluster file name.
    run_id = cluster_file.stem.removeprefix("clusters_")

    raw_dir = _DATA_DIR / "raw" / topic_slug / region
    post_texts: dict[str, str] = {}
    post_metadata: dict[str, dict[str, Any]] = {}
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf) as f:
            posts = json.load(f)
        for p in posts:
            pid = p.get("id", "")
            if not pid:
                continue
            post_texts[pid] = (
                f"{p.get('title', '') or ''}\n{p.get('body', '') or ''}".strip()
            )
            post_metadata[pid] = {
                "source": p.get("source", ""),
                "url": p.get("url", ""),
                "lang": p.get("language_detected", "en"),
                "posted_at": p.get("posted_at", ""),
                "body": p.get("body", ""),
            }

    # ── Temporal trend analysis (--temporal flag) ─────────────────
    if temporal:
        from src.pipeline.models import compute_temporal_trends

        trends = compute_temporal_trends(
            post_metadata,
            topic=topic,
            region=region,
            bucket_type="week",
        )

        typer.echo(f"\n📅 Temporal Trends ({trends.total_posts} posts, "
                   f"{len(trends.buckets)} {trends.bucket_type}s)")

        # ASCII sparkline for post volume
        if trends.buckets:
            max_count = max(b.post_count for b in trends.buckets) or 1
            bar_width = 40
            typer.echo("  Volume by week:")
            for b in trends.buckets:
                bar_len = int(b.post_count / max_count * bar_width)
                bar = "█" * bar_len
                typer.echo(f"  {b.label}  {bar} {b.post_count}")

        if trends.spikes:
            typer.echo(f"\n  ⚡ Spikes detected ({len(trends.spikes)}):")
            for s in trends.spikes:
                typer.echo(
                    f"    {s['bucket']}: {s['post_count']} posts "
                    f"(median: {s['median']}, complaints: {s['complaint_count']})"
                )

        # Save JSON
        trends_dir = _DATA_DIR / "trends" / topic_slug / region
        trends_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        trends_path = trends_dir / f"trends_{ts}.json"
        with open(trends_path, "w", encoding="utf-8") as f:
            json.dump(trends.to_dict(), f, indent=2, default=str, ensure_ascii=False)
        typer.echo(f"\n  Trends saved: {trends_path}")

    cluster_ids = [cluster_id] if cluster_id else None
    try:
        report = synthesize_run(
            topic=topic,
            region=region,
            clusters=result.clusters,
            post_texts=post_texts,
            post_metadata=post_metadata,
            provider=provider,
            model=model or None,
            dry_run=dry_run,
            force=force,
            max_cost_usd=max_cost,
            run_id=run_id,
            cluster_ids=cluster_ids,
        )
    except MissingAPIKey as e:
        env_var = "DEEPSEEK_API_KEY" if provider == "deepseek" else "ANTHROPIC_API_KEY"
        typer.echo(
            f"Missing API key: {e}\n"
            f"  → Set {env_var} in your .env file and retry.",
            err=True,
        )
        raise typer.Exit(code=1)
    except CostCapExceeded as e:
        typer.echo(f"Cost cap exceeded: {e}", err=True)
        raise typer.Exit(code=2)
    except SynthesisError as e:
        typer.echo(f"Synthesis failed: {e}", err=True)
        raise typer.Exit(code=1)

    est = report.estimate
    typer.echo(
        f"Plan: {report.clusters_processed} clusters via "
        f"{report.provider} ({report.model})"
    )
    if est is not None:
        typer.echo(
            f"  Estimated tokens: input={est.estimated_input_tokens:,}, "
            f"cached={est.estimated_cached_input_tokens:,}, "
            f"output={est.estimated_output_tokens:,}"
        )
        typer.echo(f"  Estimated cost: ${est.estimated_usd:.4f}")
        typer.echo(f"  Cap: ${max_cost:.2f}")

    if dry_run:
        typer.echo("Dry run — no API calls made.")
        return

    personas_dir = _DATA_DIR / "personas" / topic_slug / region
    journeys_dir = _DATA_DIR / "journeys" / topic_slug / region
    personas_dir.mkdir(parents=True, exist_ok=True)
    journeys_dir.mkdir(parents=True, exist_ok=True)

    for p in report.personas:
        out = personas_dir / f"{p.id}.json"
        with open(out, "w") as f:
            json.dump(p.model_dump(mode="json"), f, indent=2, default=str, ensure_ascii=False)
        unverified = [
            name for name in (
                "goals", "motivations", "pain_points",
                "preferred_channels", "behaviors",
            ) if getattr(p, name).coverage != "ok"
        ]
        tag = f" [unverified: {', '.join(unverified)}]" if unverified else ""
        typer.echo(f"  persona {p.id} -> {p.name}{tag}")

    for j in report.journeys:
        out = journeys_dir / f"{j.id}.json"
        with open(out, "w") as f:
            json.dump(j.model_dump(mode="json"), f, indent=2, default=str, ensure_ascii=False)
        thin = [s.stage for s in j.stages if s.coverage in {"thin", "none"}]
        tag = f" [thin: {', '.join(thin)}]" if thin else ""
        typer.echo(f"  journey {j.id}{tag}")

    typer.echo(
        f"Done. Actual cost: ${report.actual_cost_usd:.4f} "
        f"(input={report.total_input_tokens:,}, "
        f"cached={report.total_cached_input_tokens:,}, "
        f"output={report.total_output_tokens:,})"
    )


# ---------------------------------------------------------------------------
# Temporal & Comparative synthesis commands
# ---------------------------------------------------------------------------


def _load_synthesize_data(
    topic: str, region: str, run_id_opt: str,
) -> tuple[ClusteringResult, str, dict[str, str], dict[str, dict[str, Any]]]:
    """Shared data loading for synthesize commands.

    Returns (ClusteringResult, run_id, post_texts, post_metadata).
    ``post_metadata`` includes ``posted_at`` for temporal filtering.
    """
    import json as _json

    topic_slug = _slugify(topic)
    clusters_dir = _DATA_DIR / "clusters" / topic_slug / region
    if not clusters_dir.exists():
        typer.echo(f"No clusters at {clusters_dir}. Run mkt cluster first.", err=True)
        raise typer.Exit(code=1)

    if run_id_opt:
        cluster_file = clusters_dir / f"clusters_{run_id_opt}.json"
        if not cluster_file.exists():
            typer.echo(f"No clustering run at {cluster_file}", err=True)
            raise typer.Exit(code=1)
    else:
        files = sorted(clusters_dir.glob("clusters_*.json"))
        if not files:
            typer.echo(f"No cluster files at {clusters_dir}", err=True)
            raise typer.Exit(code=1)
        cluster_file = files[-1]

    with open(cluster_file) as f:
        result = ClusteringResult(**_json.load(f))

    run_id = cluster_file.stem.removeprefix("clusters_")

    raw_dir = _DATA_DIR / "raw" / topic_slug / region
    post_texts: dict[str, str] = {}
    post_metadata: dict[str, dict[str, Any]] = {}
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf) as f:
            posts = _json.load(f)
        for p in posts:
            pid = p.get("id", "")
            if not pid:
                continue
            post_texts[pid] = (
                f"{p.get('title', '') or ''}\n{p.get('body', '') or ''}".strip()
            )
            post_metadata[pid] = {
                "source": p.get("source", ""),
                "url": p.get("url", ""),
                "lang": p.get("language_detected", "en"),
                "posted_at": p.get("posted_at"),
            }

    return result, run_id, post_texts, post_metadata


@app.command(name="synthesize-temporal")
def synthesize_temporal_cmd(
    topic: Annotated[str, typer.Option(..., "--topic")],
    region: Annotated[str, typer.Option(..., "--region")],
    before: Annotated[
        str,
        typer.Option(
            "--before",
            help="Cutoff date (YYYY-MM-DD). Posts before this go to the before window.",
        ),
    ],
    after: Annotated[
        str,
        typer.Option(
            "--after",
            help="Cutoff date (YYYY-MM-DD). Posts on/after this go to the after window.",
        ),
    ],
    run_id_opt: Annotated[
        str,
        typer.Option(
            "--run",
            help="Specific clustering run timestamp. Latest if omitted.",
        ),
    ] = "",
    provider: Annotated[
        str,
        typer.Option("--provider", help="LLM backend. anthropic (default) or deepseek."),
    ] = "anthropic",
    model: Annotated[
        str,
        typer.Option("--model", help="Override model id."),
    ] = "",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print plan + cost estimate; no API calls."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Bypass the cost cap."),
    ] = False,
    max_cost: Annotated[
        float,
        typer.Option("--max-cost", help="Hard cost ceiling in USD."),
    ] = 10000.00,
) -> None:
    """Compare the same topic across two time windows.

    Filters posts into "before" and "after" windows using --before and
    --after cutoff dates, runs synthesis on each, and reports shifts.
    """
    if not _PIPELINE_AVAILABLE:
        typer.echo("Pipeline deps not installed.", err=True)
        raise typer.Exit(code=1)

    # Parse cutoff dates
    cutoff_before = datetime.strptime(before, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    cutoff_after = datetime.strptime(after, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    result, run_id, post_texts, post_metadata = _load_synthesize_data(
        topic, region, run_id_opt,
    )

    typer.echo(
        f"Temporal analysis: {topic} ({region}) | "
        f"Before < {before} | After >= {after}"
    )

    try:
        comparison = synthesize_temporal(
            topic=topic,
            region=region,
            cutoff_before=cutoff_before,
            cutoff_after=cutoff_after,
            clusters=result.clusters,
            post_texts=post_texts,
            post_metadata=post_metadata,
            provider=provider,
            model=model or None,
            dry_run=dry_run,
            force=force,
            max_cost_usd=max_cost,
            run_id=run_id,
        )
    except MissingAPIKey as e:
        env_var = "DEEPSEEK_API_KEY" if provider == "deepseek" else "ANTHROPIC_API_KEY"
        typer.echo(
            f"Missing API key: {e}\n"
            f"  → Set {env_var} in your .env file and retry.",
            err=True,
        )
        raise typer.Exit(code=1)
    except CostCapExceeded as e:
        typer.echo(f"Cost cap exceeded: {e}", err=True)
        raise typer.Exit(code=2)
    except SynthesisError as e:
        typer.echo(f"Synthesis failed: {e}", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo("Dry run — no API calls made.")
        return

    typer.echo(f"\nBefore window ({comparison.window_before_label}):")
    typer.echo(f"  Personas: {len(comparison.window_before)}")
    typer.echo(f"\nAfter window ({comparison.window_after_label}):")
    typer.echo(f"  Personas: {len(comparison.window_after)}")

    typer.echo(f"\nShifts detected:")
    for shift in comparison.shifts:
        typer.echo(f"  [{shift['type']}] {len(shift['claims'])} claims")
        for claim in shift["claims"][:5]:
            typer.echo(f"    - {claim}")
        if len(shift["claims"]) > 5:
            typer.echo(f"    ... and {len(shift['claims']) - 5} more")

    typer.echo(f"\nSummary: {comparison.summary}")

    # Persist result
    comparison_dir = _DATA_DIR / "comparisons" / _slugify(topic) / region
    comparison_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = comparison_dir / f"temporal_{ts}.json"
    with open(out_path, "w") as f:
        import json as _json
        _json.dump(
            comparison.model_dump(mode="json"), f,
            indent=2, default=str, ensure_ascii=False,
        )
    typer.echo(f"Saved: {out_path}")


@app.command(name="synthesize-compare")
def synthesize_compare_cmd(
    topic_a: Annotated[str, typer.Option(..., "--topic-a")],
    topic_b: Annotated[str, typer.Option(..., "--topic-b")],
    region: Annotated[str, typer.Option(..., "--region")],
    run_id_opt: Annotated[
        str,
        typer.Option(
            "--run",
            help="Specific clustering run timestamp. Latest if omitted.",
        ),
    ] = "",
    provider: Annotated[
        str,
        typer.Option("--provider", help="LLM backend. anthropic (default) or deepseek."),
    ] = "anthropic",
    model: Annotated[
        str,
        typer.Option("--model", help="Override model id."),
    ] = "",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print plan + cost estimate; no API calls."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Bypass the cost cap."),
    ] = False,
    max_cost: Annotated[
        float,
        typer.Option("--max-cost", help="Hard cost ceiling in USD."),
    ] = 10000.00,
) -> None:
    """Compare two different topics in the same region.

    Runs synthesis for each topic independently, then diffs the resulting
    personas to surface common and divergent pain points.
    """
    if not _PIPELINE_AVAILABLE:
        typer.echo("Pipeline deps not installed.", err=True)
        raise typer.Exit(code=1)

    # Load data for both topics
    result_a, run_id_a, texts_a, meta_a = _load_synthesize_data(
        topic_a, region, run_id_opt,
    )
    result_b, run_id_b, texts_b, meta_b = _load_synthesize_data(
        topic_b, region, run_id_opt,
    )

    typer.echo(
        f"Comparative analysis: {topic_a} vs {topic_b} ({region})"
    )

    try:
        comparison = synthesize_comparative(
            topic_a=topic_a,
            topic_b=topic_b,
            region=region,
            clusters_a=result_a.clusters,
            clusters_b=result_b.clusters,
            post_texts_a=texts_a,
            post_texts_b=texts_b,
            post_metadata_a=meta_a,
            post_metadata_b=meta_b,
            provider=provider,
            model=model or None,
            dry_run=dry_run,
            force=force,
            max_cost_usd=max_cost,
            run_id=run_id_a,  # use first topic's run id as base
        )
    except MissingAPIKey as e:
        env_var = "DEEPSEEK_API_KEY" if provider == "deepseek" else "ANTHROPIC_API_KEY"
        typer.echo(
            f"Missing API key: {e}\n"
            f"  → Set {env_var} in your .env file and retry.",
            err=True,
        )
        raise typer.Exit(code=1)
    except CostCapExceeded as e:
        typer.echo(f"Cost cap exceeded: {e}", err=True)
        raise typer.Exit(code=2)
    except SynthesisError as e:
        typer.echo(f"Synthesis failed: {e}", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo("Dry run — no API calls made.")
        return

    typer.echo(f"\n{topic_a}: {len(comparison.personas_a)} personas")
    typer.echo(f"{topic_b}: {len(comparison.personas_b)} personas")

    if comparison.common_pain_points:
        typer.echo(f"\nCommon pain points ({len(comparison.common_pain_points)}):")
        for pp in comparison.common_pain_points[:5]:
            typer.echo(f"  [{pp['severity']}] {pp['claim']}")
        if len(comparison.common_pain_points) > 5:
            typer.echo(f"  ... and {len(comparison.common_pain_points) - 5} more")

    if comparison.divergent_pain_points:
        typer.echo(f"\nDivergent pain points ({len(comparison.divergent_pain_points)}):")
        for pp in comparison.divergent_pain_points[:5]:
            typer.echo(f"  [{pp['severity']}] ({pp['unique_to']}) {pp['claim']}")
        if len(comparison.divergent_pain_points) > 5:
            typer.echo(
                f"  ... and {len(comparison.divergent_pain_points) - 5} more"
            )

    typer.echo(f"\nSummary: {comparison.summary}")

    # Persist result
    comparison_dir = _DATA_DIR / "comparisons" / f"{_slugify(topic_a)}_vs_{_slugify(topic_b)}" / region
    comparison_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = comparison_dir / f"comparative_{ts}.json"
    with open(out_path, "w") as f:
        import json as _json
        _json.dump(
            comparison.model_dump(mode="json"), f,
            indent=2, default=str, ensure_ascii=False,
        )
    typer.echo(f"Saved: {out_path}")


@app.command()
def export(
    topic: Annotated[str, typer.Option(..., "--topic", help="Topic slug (as used in synthesize).")],
    region: Annotated[str, typer.Option(..., "--region", help="Canonical region code, e.g. HK.")],
    output: Annotated[
        str,
        typer.Option(
            "--output", "-o",
            help="Output PDF path. Default: persona_report.pdf",
        ),
    ] = "persona_report.pdf",
    run_id_opt: Annotated[
        str,
        typer.Option(
            "--run",
            help="Specific clustering run timestamp. Uses latest synthesized data if omitted.",
        ),
    ] = "",
    persona_id: Annotated[
        str,
        typer.Option(
            "--persona",
            help="Specific persona id (e.g. persona_fc014d201975). Exports first match if omitted.",
        ),
    ] = "",
) -> None:
    """Export persona + journey as a stakeholder-ready PDF report.

    Reads synthesized persona and journey JSON files from
    data/personas/{topic}/{region}/ and data/journeys/{topic}/{region}/,
    matches persona to its journey by persona_id, and generates a
    professional PDF with pain point tables, quote callouts, sentiment
    distribution bars, and source coverage stats.
    """
    import json as _json

    from src.export.pdf import export_persona_report
    from src.schemas.synthesis import Persona, JourneyMap

    topic_slug = _slugify(topic)
    personas_dir = _DATA_DIR / "personas" / topic_slug / region
    journeys_dir = _DATA_DIR / "journeys" / topic_slug / region

    if not personas_dir.exists():
        typer.echo(
            f"No personas directory at {personas_dir}. Run mkt synthesize first.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Load persona files, optionally filtered by run_id
    persona_files = sorted(personas_dir.glob("*.json"))
    if not persona_files:
        typer.echo(f"No persona files found in {personas_dir}", err=True)
        raise typer.Exit(code=1)

    personas: list[Persona] = []
    for pf in persona_files:
        with open(pf) as f:
            data = _json.load(f)
        p = Persona(**data)
        if run_id_opt and p.run_id != run_id_opt:
            continue
        if persona_id and p.id != persona_id:
            continue
        personas.append(p)

    if not personas:
        filter_desc = []
        if run_id_opt:
            filter_desc.append(f"run={run_id_opt}")
        if persona_id:
            filter_desc.append(f"persona={persona_id}")
        typer.echo(
            f"No personas matched filters ({', '.join(filter_desc)}). "
            f"Available: {[p.name for p in persona_files]}",
            err=True,
        )
        raise typer.Exit(code=1)

    persona = personas[0]  # Take first match

    # Load matching journey
    journey: JourneyMap | None = None
    if journeys_dir.exists():
        for jf in sorted(journeys_dir.glob("*.json")):
            with open(jf) as f:
                jdata = _json.load(f)
            j = JourneyMap(**jdata)
            if j.persona_id == persona.id:
                journey = j
                break

    if journey is None:
        typer.echo(
            f"Warning: no matching journey found for persona {persona.id}",
            err=True,
        )

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Exporting persona '{persona.name}'...")
    export_persona_report(
        persona=persona,
        journey=journey,
        output_path=output_path,
        topic=topic,
        region=region,
    )
    typer.echo(f"Report saved: {output_path.resolve()}")
    if journey:
        typer.echo(
            f"  Persona: {persona.name} ({persona.cluster_size} posts, "
            f"{len(persona.pain_points.claims)} pain points)"
        )
        typer.echo(
            f"  Journey: {len(journey.stages)} stages"
        )
    else:
        typer.echo(
            f"  Persona: {persona.name} ({persona.cluster_size} posts, "
            f"{len(persona.pain_points.claims)} pain points)"
        )


@app.command()
def analyze(
    topic: Annotated[
        str, typer.Option(..., "--topic", help="Search term or app/product to analyze.")
    ],
    region: Annotated[
        str, typer.Option(..., "--region", help="Canonical region code, e.g. HK.")
    ],
    sources: Annotated[
        str,
        typer.Option(
            "--sources",
            help="Comma-separated source ids. Default: app_store_hk,google_play_hk,reddit_old.",
        ),
    ] = "",
    limit: Annotated[
        int, typer.Option("--limit", min=1, help="Max posts per source.")
    ] = 100,
    since: Annotated[
        str, typer.Option("--since", help="Relative window, e.g. 180d, 6m.")
    ] = "180d",
    subreddits: Annotated[
        str,
        typer.Option("--subreddits", help="Comma-separated subreddits (for reddit_old)."),
    ] = "",
    generate_personas: Annotated[
        bool,
        typer.Option(
            "--personas/--no-personas",
            help="Generate personas via Claude (needs ANTHROPIC_API_KEY).",
        ),
    ] = False,
) -> None:
    """Run the full pipeline: scrape -> embed -> cluster.

    One command from zero to insights. Optionally generates personas
    and journey maps if --personas is set and Claude API key is available.
    """
    if not _PIPELINE_AVAILABLE:
        typer.echo(
            "Pipeline deps not installed. "
            "Run: pip install sentence-transformers duckdb umap-learn hdbscan scikit-learn",
            err=True,
        )
        raise typer.Exit(code=1)

    topic_slug = _slugify(topic)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Determine sources
    if sources:
        source_ids = [s.strip() for s in sources.split(",") if s.strip()]
    else:
        source_ids = ["app_store_hk", "google_play_hk", "reddit_old"]

    unknown = [s for s in source_ids if s not in available_sources()]
    if unknown:
        typer.echo(
            f"Unknown sources: {unknown}. Available: {available_sources()}",
            err=True,
        )
        raise typer.Exit(code=2)

    since_dt = parse_since(since)

    # ── Phase 1: Scrape ──────────────────────────────────────────────────
    typer.echo(f"\n{'='*60}")
    typer.echo(f"Analyzing: {topic} ({region})")
    typer.echo(f"Sources: {', '.join(source_ids)}")
    typer.echo(f"{'='*60}\n")

    with DedupIndex(_DATA_DIR / "dedup.sqlite") as index:
        for source_id in source_ids:
            writer = RunWriter(
                data_dir=_DATA_DIR,
                topic_slug=topic_slug,
                region=region,
                source=source_id,
                run_id=run_id,
            )
            scraper_kwargs = {}
            if source_id == "reddit_old" and subreddits:
                scraper_kwargs["subreddits"] = [
                    s.strip() for s in subreddits.split(",") if s.strip()
                ]
            scraper = get_scraper(source_id, **scraper_kwargs)
            emitted = 0
            try:
                typer.echo(f"  [{source_id}] scraping...", nl=False)
                for post in scraper.search(topic, since=since_dt, limit=limit):
                    index.mark_seen(
                        source=source_id,
                        source_post_id=post.id,
                        region=region,
                        topic_slug=topic_slug,
                    )
                    writer.add(post)
                    emitted += 1
                typer.echo(f" {emitted} posts")
            finally:
                close = getattr(scraper, "close", None)
                if callable(close):
                    close()
            writer.finalize()

    # ── Phase 2: Embed ───────────────────────────────────────────────────
    import json as _json

    typer.echo(f"\n  [embed] Loading model + encoding...", nl=False)
    raw_dir = _DATA_DIR / "raw" / topic_slug / region
    db_path = _DATA_DIR / "embeddings.duckdb"
    store = EmbeddingStore(db_path=db_path)

    total = 0
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf) as f:
            posts_data = _json.load(f)
        posts = [RawPost(**p) for p in posts_data]
        n = store.embed_posts(posts, topic=topic, region=region)
        total += n
    store.close()
    typer.echo(f" {total} vectors")

    if total == 0:
        typer.echo("  No posts to embed. Check your sources and topic.", err=True)
        raise typer.Exit(code=1)

    # ── Phase 3: Cluster ─────────────────────────────────────────────────
    import duckdb as _duckdb
    import numpy as _np

    typer.echo(f"  [cluster] UMAP + HDBSCAN...", nl=False)
    cfg = load_config(None)
    con = _duckdb.connect(str(db_path))
    con.execute("LOAD vss;")
    con.execute("SET hnsw_enable_experimental_persistence = true")
    rows = con.execute(
        "SELECT post_id, source, vector FROM embeddings WHERE topic = ? AND region = ?",
        [topic, region],
    ).fetchall()

    vectors = _np.array([_np.array(r[2]) for r in rows])
    post_ids_vec = [r[0] for r in rows]
    source_map = {r[0]: r[1] for r in rows}

    # Load post texts for c-TF-IDF
    post_texts: dict[str, str] = {}
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf) as f:
            raw_posts = _json.load(f)
        for p in raw_posts:
            pid = p.get("id", "")
            text = f"{p.get('title', '') or ''}\n{p.get('body', '') or ''}"
            if pid and pid in set(post_ids_vec):
                post_texts[pid] = text

    from src.lang import get_tokenizer as _get_tokenizer
    result = cluster_embeddings(
        vectors, post_ids_vec, topic, region,
        config=cfg,
        source_map=source_map,
        post_texts=post_texts if post_texts else None,
        tokenizer=_get_tokenizer(region),
    )

    out_dir = _DATA_DIR / "clusters" / topic_slug / region
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"clusters_{ts}.json"
    with open(out_path, "w") as f:
        _json.dump(result.model_dump(mode="json"), f, indent=2, default=str)

    npct = result.noise_count / len(post_ids_vec) * 100 if post_ids_vec else 0
    typer.echo(f" {len(result.clusters)} clusters, {result.noise_count} noise ({npct:.0f}%)")
    con.close()

    # ── Phase 4: Results ─────────────────────────────────────────────────
    typer.echo(f"\n{'='*60}")
    typer.echo(f"Results: {topic} ({region})")
    typer.echo(f"Posts: {total} | Clusters: {len(result.clusters)} | Noise: {npct:.0f}%")
    typer.echo(f"{'='*60}\n")

    for c in result.clusters:
        kws = ", ".join(c.keyword_summary[:6])
        srcs = ", ".join(f"{k}={v}" for k, v in c.source_distribution.items())
        typer.echo(f"  {c.cluster_id} ({c.size} posts): {kws}")
        typer.echo(f"    Sources: {srcs}")
        rep = c.representative_post_ids[0] if c.representative_post_ids else None
        if rep and rep in post_texts:
            snippet = post_texts[rep][:120].replace("\n", " ")
            typer.echo(f"    \"{snippet}...\"")
        typer.echo()

    typer.echo(f"Cluster result: {out_path}")

    # ── Phase 5: Personas (optional) ─────────────────────────────────────
    import os as _os

    if generate_personas:
        if not _os.environ.get("ANTHROPIC_API_KEY"):
            typer.echo("\n  [persona] Skipped — ANTHROPIC_API_KEY not set", err=True)
        else:
            typer.echo("\n  [persona] Generating via Claude...")
            for c in result.clusters:
                try:
                    p = generate_persona(c, post_texts, {})
                    personas_out = _DATA_DIR / "personas" / topic_slug / region
                    personas_out.mkdir(parents=True, exist_ok=True)
                    p_path = personas_out / f"{p.id}.json"
                    with open(p_path, "w") as pf:
                        _json.dump(p.model_dump(mode="json"), pf, indent=2, default=str)
                    typer.echo(f"    {p.name}: {p.one_liner}")

                    jm = generate_journey(p, c, post_texts, {})
                    journeys_out = _DATA_DIR / "journeys" / topic_slug / region
                    journeys_out.mkdir(parents=True, exist_ok=True)
                    j_path = journeys_out / f"{jm.id}.json"
                    with open(j_path, "w") as jf:
                        _json.dump(jm.model_dump(mode="json"), jf, indent=2, default=str)
                    typer.echo(f"      Journey: {len(jm.stages)} stages")
                except Exception as e:
                    typer.echo(f"    Failed: {e}")


if __name__ == "__main__":
    app()
