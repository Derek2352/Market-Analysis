"""analyze command — injected into cli.py"""
import json as _json
import os as _os

import duckdb as _duckdb
import numpy as _np
import typer


@app.command()  # type: ignore[name-defined]  # noqa: F821
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
    if not _PIPELINE_AVAILABLE:  # type: ignore[name-defined]  # noqa: F821
        typer.echo(
            "Pipeline deps not installed. "
            "Run: pip install sentence-transformers duckdb umap-learn hdbscan scikit-learn",
            err=True,
        )
        raise typer.Exit(code=1)

    topic_slug = _slugify(topic)  # type: ignore[name-defined]  # noqa: F821
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Determine sources
    if sources:
        source_ids = [s.strip() for s in sources.split(",") if s.strip()]
    else:
        source_ids = ["app_store_hk", "google_play_hk", "reddit_old"]

    unknown = [s for s in source_ids if s not in available_sources()]  # type: ignore[name-defined]  # noqa: F821
    if unknown:
        typer.echo(
            f"Unknown sources: {unknown}. Available: {available_sources()}",  # type: ignore[name-defined]  # noqa: F821
            err=True,
        )
        raise typer.Exit(code=2)

    since_dt = parse_since(since)  # type: ignore[name-defined]  # noqa: F821

    # ── Phase 1: Scrape ──────────────────────────────────────────────────
    typer.echo(f"\n{'='*60}")
    typer.echo(f"Analyzing: {topic} ({region})")
    typer.echo(f"Sources: {', '.join(source_ids)}")
    typer.echo(f"{'='*60}\n")

    with DedupIndex(_DATA_DIR / "dedup.sqlite") as index:  # type: ignore[name-defined]  # noqa: F821
        for source_id in source_ids:
            writer = RunWriter(  # type: ignore[name-defined]  # noqa: F821
                data_dir=_DATA_DIR,  # type: ignore[name-defined]  # noqa: F821
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
            scraper = get_scraper(source_id, **scraper_kwargs)  # type: ignore[name-defined]  # noqa: F821
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
    typer.echo(f"\n  [embed] Loading model + encoding...", nl=False)
    raw_dir = _DATA_DIR / "raw" / topic_slug / region  # type: ignore[name-defined]  # noqa: F821
    db_path = _DATA_DIR / "embeddings.duckdb"  # type: ignore[name-defined]  # noqa: F821
    store = EmbeddingStore(db_path=db_path)  # type: ignore[name-defined]  # noqa: F821

    total = 0
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf) as f:
            posts_data = _json.load(f)
        posts = [RawPost(**p) for p in posts_data]  # type: ignore[name-defined]  # noqa: F821
        n = store.embed_posts(posts, topic=topic, region=region)
        total += n
    store.close()
    typer.echo(f" {total} vectors")

    if total == 0:
        typer.echo("  No posts to embed. Check your sources and topic.", err=True)
        raise typer.Exit(code=1)

    # ── Phase 3: Cluster ─────────────────────────────────────────────────
    typer.echo(f"  [cluster] UMAP + HDBSCAN...", nl=False)
    cfg = load_config(None)  # type: ignore[name-defined]  # noqa: F821
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
    result = cluster_embeddings(  # type: ignore[name-defined]  # noqa: F821
        vectors, post_ids_vec, topic, region,
        config=cfg,
        source_map=source_map,
        post_texts=post_texts if post_texts else None,
        tokenizer=_get_tokenizer(region),
    )

    out_dir = _DATA_DIR / "clusters" / topic_slug / region  # type: ignore[name-defined]  # noqa: F821
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
    if generate_personas:
        if not _os.environ.get("ANTHROPIC_API_KEY"):
            typer.echo("\n  [persona] Skipped — ANTHROPIC_API_KEY not set", err=True)
        else:
            typer.echo("\n  [persona] Generating via Claude...")
            for c in result.clusters:
                try:
                    p = generate_persona(c, post_texts, {})  # type: ignore[name-defined]  # noqa: F821
                    personas_out = _DATA_DIR / "personas" / topic_slug / region  # type: ignore[name-defined]  # noqa: F821
                    personas_out.mkdir(parents=True, exist_ok=True)
                    p_path = personas_out / f"{p.id}.json"
                    with open(p_path, "w") as pf:
                        _json.dump(p.model_dump(mode="json"), pf, indent=2, default=str)
                    typer.echo(f"    {p.name}: {p.one_liner}")

                    jm = generate_journey(p, c, post_texts, {})  # type: ignore[name-defined]  # noqa: F821
                    journeys_out = _DATA_DIR / "journeys" / topic_slug / region  # type: ignore[name-defined]  # noqa: F821
                    journeys_out.mkdir(parents=True, exist_ok=True)
                    j_path = journeys_out / f"{jm.id}.json"
                    with open(j_path, "w") as jf:
                        _json.dump(jm.model_dump(mode="json"), jf, indent=2, default=str)
                    typer.echo(f"      Journey: {len(jm.stages)} stages")
                except Exception as e:
                    typer.echo(f"    Failed: {e}")
