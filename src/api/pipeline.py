"""Pipeline orchestrator for the API.

Runs scrape → embed → cluster → synthesize for one (topic, region) tuple,
publishing progress to the run's EventLog as it goes. CPU/IO-bound stages
run in ``run_in_executor`` so the FastAPI event loop stays responsive
during a scrape.

Concurrency model: one-at-a-time. A single asyncio.Lock serialises every
spawned ``execute_run`` so multiple POST /runs in flight queue safely
behind each other.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import structlog

from src.api.events import EventLog
from src.api.runs import RunState
from src.regions.registry import get_region
from src.schemas.cluster import Cluster, ClusteringResult
from src.scrape.registry import available_sources, get_scraper
from src.scrape.utils.dedup import DedupIndex
from src.scrape.utils.writer import RunWriter

_log = structlog.get_logger(__name__)

# Single lock shared across the process — enforces one-at-a-time runs.
_executor_lock = asyncio.Lock()


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "untitled"


async def execute_run(state: RunState, data_dir: Path) -> None:
    """The single coroutine driving one run through all four stages."""
    events = state.events
    params = state.params
    loop = asyncio.get_running_loop()

    # Stay 'queued' until we get the lock — multiple runs serialise here.
    async with _executor_lock:
        state.set_status("running")
        try:
            posts_count = await _run_in_executor(
                loop, _scrape_step, state, data_dir
            )
            state.set_counts(posts=posts_count)

            await _run_in_executor(loop, _embed_step, state, data_dir)

            clusters = await _run_in_executor(
                loop, _cluster_step, state, data_dir
            )
            state.set_counts(clusters=len(clusters))

            personas, journeys, cost = await _run_in_executor(
                loop, _synthesize_step, state, data_dir, clusters
            )
            state.set_counts(personas=personas, journeys=journeys)

            events.emit("done", {
                "run_id": state.summary.run_id,
                "personas": personas,
                "journeys": journeys,
                "cost_usd": round(cost, 4),
                "counts": state.summary.counts.model_dump(),
            })
            state.set_status("succeeded")
        except Exception as e:  # noqa: BLE001 — we want every failure to land cleanly
            _log.warning("api.run_failed", run_id=state.summary.run_id, error=str(e))
            events.emit("error", {
                "run_id": state.summary.run_id,
                "stage": (state.progress.stage if state.progress else None),
                "error": str(e),
            })
            state.set_status("failed", error=str(e))


async def _run_in_executor(loop, fn, *args):
    return await loop.run_in_executor(None, fn, *args)


# ---------------------------------------------------------------------------
# Stage 1 — scrape
# ---------------------------------------------------------------------------


def _scrape_step(state: RunState, data_dir: Path) -> int:
    """Scrape every requested source for the topic. Returns total posts."""
    events = state.events
    params = state.params
    topic = params["topic"]
    region = params["region"]
    since_days = params["since_days"]
    limit = params["limit_per_source"]
    requested = list(params.get("sources") or [])

    # Resolve sources: empty list -> region defaults.
    region_cfg = get_region(region)
    if not requested:
        requested = region_cfg.default_source_ids()

    # Drop any source the scraper registry doesn't implement, with a warning.
    impls = set(available_sources())
    unknown = [s for s in requested if s not in impls]
    sources = [s for s in requested if s in impls]
    if unknown:
        events.emit("progress", {
            "stage": "scrape", "pct": 0.0,
            "message": f"WARNING: skipping unimplemented sources {unknown}",
        })

    # Surface ToS-prohibited sources in the stream so the UI sees them.
    by_id = {s.source_id: s for s in region_cfg.sources}
    for sid in sources:
        sc = by_id.get(sid)
        if sc and sc.tos_scraping_stance.value == "prohibited":
            events.emit("progress", {
                "stage": "scrape",
                "pct": 0.0,
                "message": (
                    f"WARNING: source {sid!r} has tos_scraping_stance=prohibited "
                    f"(last checked {sc.last_checked}). You are responsible for "
                    f"compliance under your jurisdiction."
                ),
            })

    if not sources:
        events.emit("stage_start", {"stage": "scrape", "message": "No scrapers to run"})
        events.emit("stage_done", {"stage": "scrape", "message": "0 posts scraped"})
        return 0

    events.emit("stage_start", {
        "stage": "scrape",
        "message": f"Scraping {len(sources)} source(s): {', '.join(sources)}",
    })

    topic_slug = _slugify(topic)
    run_id = state.summary.run_id
    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    total = 0
    failed: list[dict[str, str]] = []  # [{source, error}] — per-source failures
    with DedupIndex(data_dir / "dedup.sqlite") as index:
        for s_idx, source_id in enumerate(sources):
            state.set_progress(
                "scrape", s_idx / len(sources),
                f"Scraping {source_id}…",
            )
            events.emit("progress", {
                "stage": "scrape",
                "pct": s_idx / len(sources),
                "message": f"Scraping {source_id}…",
            })
            writer = RunWriter(
                data_dir=data_dir, topic_slug=topic_slug,
                region=region, source=source_id, run_id=run_id,
            )
            try:
                scraper = get_scraper(source_id)
            except Exception as exc:  # noqa: BLE001
                # Couldn't even construct the scraper — log and skip.
                _log.warning(
                    "scrape.source.error", source=source_id,
                    error=str(exc), emitted=0,
                )
                events.emit("scrape.source.error", {
                    "stage": "scrape",
                    "source": source_id,
                    "error": str(exc),
                    "emitted": 0,
                    "message": f"⚠ {source_id}: {exc}",
                })
                failed.append({"source": source_id, "error": str(exc)})
                writer.finalize()
                continue
            emitted = 0
            try:
                for post in scraper.search(topic, since=since_dt, limit=limit):
                    is_new = index.mark_seen(
                        source=source_id, source_post_id=post.id,
                        region=region, topic_slug=topic_slug,
                    )
                    if is_new:
                        writer.add(post)
                        emitted += 1
                        if emitted % 25 == 0:
                            events.emit("progress", {
                                "stage": "scrape",
                                "pct": (s_idx + emitted / max(limit, 1)) / len(sources),
                                "message": f"Scraping {source_id}: {emitted} new posts",
                            })
            except Exception as exc:  # noqa: BLE001 — mirror CLI: one source dying must not kill the run
                _log.warning(
                    "scrape.source.error", source=source_id,
                    error=str(exc), emitted=emitted,
                )
                events.emit("scrape.source.error", {
                    "stage": "scrape",
                    "source": source_id,
                    "error": str(exc),
                    "emitted": emitted,
                    "message": f"⚠ {source_id}: {exc}",
                })
                failed.append({"source": source_id, "error": str(exc)})
            finally:
                close = getattr(scraper, "close", None)
                if callable(close):
                    close()
                writer.finalize()
            total += emitted
            events.emit("progress", {
                "stage": "scrape",
                "pct": (s_idx + 1) / len(sources),
                "message": f"{source_id}: {emitted} new posts",
            })

    msg = f"Scraped {total} new posts across {len(sources)} source(s)"
    if failed:
        ok = len(sources) - len(failed)
        msg += f" — {len(failed)} source(s) failed, {ok} ok"
    events.emit("stage_done", {"stage": "scrape", "message": msg, "failed_sources": failed})

    # Only hard-fail if *every* source failed AND none produced posts.
    # Otherwise the run continues with whatever did succeed.
    if total == 0 and failed and len(failed) == len(sources):
        raise RuntimeError(
            f"All {len(sources)} source(s) failed: "
            + "; ".join(f"{f['source']}: {f['error']}" for f in failed)
        )
    return total


# ---------------------------------------------------------------------------
# Stage 2 — embed
# ---------------------------------------------------------------------------


def _embed_step(state: RunState, data_dir: Path) -> int:
    """Load scraped posts, embed via BGE-M3, store in DuckDB+VSS."""
    from src.pipeline.embed import EmbeddingStore
    from src.schemas.raw import RawPost

    events = state.events
    params = state.params
    topic = params["topic"]
    region = params["region"]
    topic_slug = _slugify(topic)

    raw_dir = data_dir / "raw" / topic_slug / region
    files = [
        p for p in sorted(raw_dir.glob("*.json"))
        if not p.name.endswith("._run.json")
    ]
    if not files:
        events.emit("stage_done", {"stage": "embed", "message": "Nothing to embed"})
        return 0

    events.emit("stage_start", {
        "stage": "embed",
        "message": f"Embedding posts from {len(files)} scrape file(s) with BGE-M3…",
    })

    store = EmbeddingStore(db_path=data_dir / "embeddings.duckdb")
    total = 0
    try:
        for i, rf in enumerate(files):
            with open(rf, encoding="utf-8") as f:
                posts = [RawPost(**p) for p in json.load(f)]
            n = store.embed_posts(posts, topic=topic, region=region)
            total += n
            state.set_progress(
                "embed", (i + 1) / len(files),
                f"Embedded {n} new vectors from {rf.name}",
            )
            events.emit("progress", {
                "stage": "embed",
                "pct": (i + 1) / len(files),
                "message": f"{rf.name}: {n} new vectors",
            })
    finally:
        store.close()

    events.emit("stage_done", {
        "stage": "embed", "message": f"Embedded {total} new posts",
    })
    return total


# ---------------------------------------------------------------------------
# Stage 3 — cluster
# ---------------------------------------------------------------------------


def _cluster_step(state: RunState, data_dir: Path) -> list[Cluster]:
    """UMAP + HDBSCAN over the embeddings. Persists clusters_{run_id}.json."""
    import duckdb
    import numpy as np

    from src.pipeline.cluster import cluster_embeddings, load_config

    events = state.events
    params = state.params
    topic = params["topic"]
    region = params["region"]
    topic_slug = _slugify(topic)
    run_id = state.summary.run_id

    events.emit("stage_start", {
        "stage": "cluster", "message": "Loading embeddings and running UMAP+HDBSCAN…",
    })

    db_path = data_dir / "embeddings.duckdb"
    if not db_path.exists():
        events.emit("stage_done", {"stage": "cluster", "message": "No embeddings — skipping cluster"})
        return []

    cfg = load_config(None)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("LOAD vss;")
        rows = con.execute(
            "SELECT post_id, source, vector, topic FROM embeddings "
            "WHERE topic = ? AND region = ?",
            [topic, region],
        ).fetchall()
    finally:
        con.close()
    if not rows:
        events.emit("stage_done", {"stage": "cluster", "message": "No vectors for this topic/region"})
        return []

    vectors = np.array([np.array(r[2]) for r in rows])
    post_ids = [r[0] for r in rows]
    sources = [r[1] for r in rows]
    source_map = dict(zip(post_ids, sources))

    # Load post texts for c-TF-IDF keywords.
    raw_dir = data_dir / "raw" / topic_slug / region
    post_texts: dict[str, str] = {}
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf, encoding="utf-8") as f:
            posts = json.load(f)
        for p in posts:
            pid = p.get("id", "")
            text = f"{p.get('title', '') or ''}\n{p.get('body', '') or ''}"
            if pid:
                post_texts[pid] = text

    state.set_progress("cluster", 0.5, "Running clustering algorithm…")
    from src.lang import get_tokenizer as _get_tokenizer
    result: ClusteringResult = cluster_embeddings(
        vectors, post_ids, topic, region,
        config=cfg, source_map=source_map,
        post_texts=post_texts if post_texts else None,
        tokenizer=_get_tokenizer(region),
    )

    out_dir = data_dir / "clusters" / topic_slug / region
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"clusters_{run_id}.json"
    out_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    events.emit("stage_done", {
        "stage": "cluster",
        "message": f"{len(result.clusters)} clusters, {result.noise_count} noise posts",
    })
    return list(result.clusters)


# ---------------------------------------------------------------------------
# Stage 4 — synthesize
# ---------------------------------------------------------------------------


def _synthesize_step(
    state: RunState, data_dir: Path, clusters: list[Cluster]
) -> tuple[int, int, float]:
    """Generate Persona + Journey JSON per cluster via the LLM."""
    from src.pipeline.synthesize import (
        build_client, generate_journey, generate_persona,
    )

    events = state.events
    params = state.params
    topic = params["topic"]
    region = params["region"]
    provider = params.get("provider", "anthropic")
    max_cost = float(params.get("max_cost_usd", 4.00))
    force = bool(params.get("force", False))
    topic_slug = _slugify(topic)
    run_id = state.summary.run_id

    if not clusters:
        events.emit("stage_done", {
            "stage": "synthesize", "message": "No clusters — nothing to synthesize",
        })
        return 0, 0, 0.0

    events.emit("stage_start", {
        "stage": "synthesize",
        "message": f"Synthesizing {len(clusters)} cluster(s) via {provider}…",
    })

    # Load post texts + metadata once, reuse across clusters.
    raw_dir = data_dir / "raw" / topic_slug / region
    post_texts: dict[str, str] = {}
    post_metadata: dict[str, dict[str, Any]] = {}
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        with open(rf, encoding="utf-8") as f:
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
            }

    client = build_client(provider)
    personas_dir = data_dir / "personas" / topic_slug / region
    journeys_dir = data_dir / "journeys" / topic_slug / region
    personas_dir.mkdir(parents=True, exist_ok=True)
    journeys_dir.mkdir(parents=True, exist_ok=True)

    # Crude pre-call cost guard reusing the same estimator the CLI uses.
    from src.pipeline.synthesize import estimate_cost
    est = estimate_cost(
        clusters, client=client, post_texts=post_texts,
        post_metadata=post_metadata, region=region,
    )
    if est.estimated_usd > max_cost and not force:
        raise RuntimeError(
            f"Estimated cost ${est.estimated_usd:.4f} exceeds cap ${max_cost:.2f}. "
            f"Re-submit with force=true or raise max_cost_usd."
        )

    cost = 0.0
    personas_done = 0
    journeys_done = 0
    pricing = client.pricing(client.default_model)

    try:
        for i, cluster in enumerate(clusters):
            state.set_progress(
                "synthesize", i / len(clusters),
                f"Synthesizing cluster {cluster.cluster_id}…",
            )
            events.emit("progress", {
                "stage": "synthesize",
                "pct": i / len(clusters),
                "message": f"Cluster {cluster.cluster_id} ({cluster.size} posts)…",
            })
            persona, pack, u1 = generate_persona(
                cluster, post_texts, post_metadata, region,
                client=client, run_id=run_id,
            )
            cost += pricing.cost(u1)
            (personas_dir / f"{persona.id}.json").write_text(
                json.dumps(persona.model_dump(mode="json"),
                           ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            personas_done += 1

            journey, u2 = generate_journey(
                persona, pack, client=client, run_id=run_id,
            )
            cost += pricing.cost(u2)
            (journeys_dir / f"{journey.id}.json").write_text(
                json.dumps(journey.model_dump(mode="json"),
                           ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            journeys_done += 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    events.emit("stage_done", {
        "stage": "synthesize",
        "message": (
            f"Generated {personas_done} persona(s), {journeys_done} journey(s). "
            f"Cost ≈ ${cost:.4f}."
        ),
    })
    return personas_done, journeys_done, cost
