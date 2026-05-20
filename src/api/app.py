"""FastAPI app — Phase 5A backend.

Endpoints:
  POST   /runs                                -> RunCreated
  GET    /runs                                -> list[RunSummary]
  GET    /runs/{run_id}                       -> RunDetail
  GET    /runs/{run_id}/stream                -> text/event-stream
  GET    /runs/{run_id}/personas              -> list[Persona]
  GET    /runs/{run_id}/journeys/{persona_id} -> JourneyMap
  GET    /runs/{run_id}/doc/{doc_id}          -> DocResponse
  DELETE /runs/{run_id}                       -> OkResponse   (cancel/cleanup helper)

Pipeline runs serialised behind a single asyncio.Lock — multiple POSTs
are accepted (each gets a run_id and queued status) but execute one at a
time. SSE clients can subscribe to in-flight or completed runs; the
``replay`` stream emits the full event history then tails live events.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.util_stdio import force_utf8_stdio

force_utf8_stdio()  # Windows: structlog → uvicorn stdout would crash on cp1252.
load_dotenv()

from src.api.models import (
    DocResponse,
    OkResponse,
    RegionResponse,
    RunCreated,
    RunDetail,
    RunRequest,
    RunSummary,
    SourceInfo,
)
from src.api.pipeline import execute_run
from src.api.runs import RunStore
from src.schemas.synthesis import JourneyMap, Persona

_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _ROOT / "data"
_RUNS_ROOT = _DATA_DIR / "runs"


app = FastAPI(
    title="Market Analytics API",
    description=(
        "Local API for running the persona + journey synthesis pipeline. "
        "Phase 5A — exposed at http://localhost:8000."
    ),
    version="0.1.0",
)

# The UI runs at http://localhost:3000 by default; in dev we permit it to call
# this API from a different origin. No public exposure — local-only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store = RunStore(_RUNS_ROOT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "untitled"


def _persona_dir(run: RunDetail | RunSummary) -> Path:
    return _DATA_DIR / "personas" / _slugify(run.topic) / run.region


def _journey_dir(run: RunDetail | RunSummary) -> Path:
    return _DATA_DIR / "journeys" / _slugify(run.topic) / run.region


def _raw_dir(run: RunDetail | RunSummary) -> Path:
    return _DATA_DIR / "raw" / _slugify(run.topic) / run.region


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", response_model=OkResponse, tags=["meta"])
def health() -> OkResponse:
    return OkResponse(message="ok")


@app.get("/regions", response_model=list[RegionResponse], tags=["meta"])
def list_regions() -> list[RegionResponse]:
    """List regions wired in this build, with implemented sources per region.

    Lets the launcher UI render real source lists per region instead of a
    hardcoded table that drifts every time a scraper lands.
    """
    from src.regions.registry import REGIONS as _REGIONS
    from src.scrape.registry import available_sources

    implemented = set(available_sources())
    out: list[RegionResponse] = []
    for rid in sorted(_REGIONS):
        cfg = _REGIONS[rid]
        default_sources: list[SourceInfo] = []
        opt_in_sources: list[SourceInfo] = []
        for s in cfg.sources:
            if s.excluded_by_constraint:
                continue
            if s.source_id not in implemented:
                continue
            info = SourceInfo(
                source_id=s.source_id,
                category=s.category.value,
                priority=s.priority,
                default_enabled=s.default_enabled,
                tos_scraping_stance=s.tos_scraping_stance.value,
                last_verified_working=(
                    str(s.last_verified_working) if s.last_verified_working else None
                ),
                notes=s.notes,
            )
            if s.default_enabled:
                default_sources.append(info)
            else:
                opt_in_sources.append(info)
        # Skip regions with no implemented sources at all
        if not (default_sources or opt_in_sources):
            continue
        out.append(
            RegionResponse(
                region_id=cfg.region_id,
                display_name=cfg.display_name,
                primary_languages=list(cfg.primary_languages),
                default_sources=sorted(default_sources, key=lambda x: x.priority),
                opt_in_sources=sorted(opt_in_sources, key=lambda x: x.priority),
            )
        )
    return out


@app.post("/runs", response_model=RunCreated, status_code=202, tags=["runs"])
async def create_run(request: RunRequest) -> RunCreated:
    """Kick off a new pipeline run; returns immediately with the run_id."""
    state = _store.create(request)
    # The execute_run coroutine will block on the shared lock if another
    # run is in flight; until then this run stays 'queued'.
    asyncio.create_task(execute_run(state, _DATA_DIR))
    return RunCreated(
        run_id=state.summary.run_id,
        status="queued",
        stream_url=f"/runs/{state.summary.run_id}/stream",
    )


@app.get("/runs", response_model=list[RunSummary], tags=["runs"])
def list_runs() -> list[RunSummary]:
    return _store.list()


@app.get("/runs/{run_id}", response_model=RunDetail, tags=["runs"])
def get_run(run_id: str) -> RunDetail:
    state = _store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return state.to_detail()


@app.delete("/runs/{run_id}", response_model=OkResponse, tags=["runs"])
def delete_run(run_id: str) -> OkResponse:
    state = _store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if state.summary.status in {"queued", "running"}:
        state.set_status("cancelled", error="cancelled by client")
        state.events.emit("cancelled", {"run_id": run_id})
    return OkResponse(message=f"run {run_id} marked cancelled")


@app.get("/runs/{run_id}/stream", tags=["runs"])
async def stream_run(run_id: str) -> StreamingResponse:
    state = _store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    async def producer() -> AsyncIterator[str]:
        # Keep-alive ticker for proxies that close idle connections.
        keepalive_task = None
        try:
            sub = state.events.subscribe()
            async for ev in sub:
                yield ev.encode()
                # Yield control so other tasks (including a slow producer)
                # can pre-empt; without this an instantly-replayed log can
                # back-pressure the writer.
                await asyncio.sleep(0)
        finally:
            if keepalive_task is not None:
                keepalive_task.cancel()

    return StreamingResponse(
        producer(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get(
    "/runs/{run_id}/personas",
    response_model=list[Persona],
    tags=["personas"],
)
def list_personas(run_id: str) -> list[Persona]:
    state = _store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    out: list[Persona] = []
    pdir = _persona_dir(state.summary)
    if not pdir.exists():
        return out
    for f in sorted(pdir.glob("persona_*.json")):
        try:
            out.append(Persona(**json.loads(f.read_text(encoding="utf-8"))))
        except Exception:
            continue
    # Filter to this run's personas — multiple runs share the topic/region
    # directory, so we only return the ones whose run_id matches.
    return [p for p in out if p.run_id == run_id]


@app.get(
    "/runs/{run_id}/journeys/{persona_id}",
    response_model=JourneyMap,
    tags=["personas"],
)
def get_journey(run_id: str, persona_id: str) -> JourneyMap:
    state = _store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    jdir = _journey_dir(state.summary)
    if not jdir.exists():
        raise HTTPException(status_code=404, detail="no journeys for this run")
    for f in sorted(jdir.glob("journey_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("persona_id") == persona_id and data.get("run_id") == run_id:
            return JourneyMap(**data)
    raise HTTPException(
        status_code=404,
        detail=f"no journey found for persona {persona_id} in run {run_id}",
    )


@app.get(
    "/runs/{run_id}/doc/{doc_id}",
    response_model=DocResponse,
    tags=["personas"],
)
def get_doc(run_id: str, doc_id: str) -> DocResponse:
    """Resolve a citation doc_id back to its source post.

    doc_id is sha256(post_id)[:12] prefixed with 'doc_'. We walk this run's
    raw post files, hash each id, and return the matching post.
    """
    state = _store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    raw_dir = _raw_dir(state.summary)
    if not raw_dir.exists():
        raise HTTPException(status_code=404, detail="no scraped posts for this run")

    # Look up the per-source category once, for the UI icons.
    from src.regions.registry import get_region as _get_region
    try:
        region_cfg = _get_region(state.summary.region)
        source_to_cat = {s.source_id: s.category.value for s in region_cfg.sources}
    except KeyError:
        source_to_cat = {}

    target = doc_id.removeprefix("doc_")
    for rf in sorted(raw_dir.glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        try:
            posts = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for p in posts:
            pid = p.get("id", "")
            short = hashlib.sha256(pid.encode("utf-8")).hexdigest()[:12]
            if short != target:
                continue
            source_id = p.get("source", "")
            return DocResponse(
                doc_id=doc_id,
                post_id=pid,
                source=source_id,
                source_category=source_to_cat.get(source_id, ""),
                url=p.get("url", ""),
                title=p.get("title"),
                body=p.get("body", ""),
                language=p.get("language_detected") or p.get("language", ""),
                posted_at=p.get("posted_at"),
            )
    raise HTTPException(
        status_code=404,
        detail=f"doc_id {doc_id} not found in run {run_id}'s scraped posts",
    )
