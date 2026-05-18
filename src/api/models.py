"""Pydantic request and response models for the FastAPI app.

Every endpoint returns one of these — no raw dicts cross the wire.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Status values are stable strings; the UI keys off them.
RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
PipelineStage = Literal["scrape", "embed", "cluster", "synthesize"]
Provider = Literal["anthropic", "deepseek"]


class RunRequest(BaseModel):
    """POST /runs body."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=200)
    region: str = Field(min_length=2, max_length=8)
    sources: list[str] = Field(default_factory=list)   # empty = region default
    since_days: int = Field(default=90, ge=1, le=730)
    provider: Provider = "anthropic"
    max_cost_usd: float = Field(default=4.00, ge=0.01, le=50.00)
    force: bool = False
    limit_per_source: int = Field(default=500, ge=1, le=5000)


class RunCreated(BaseModel):
    """POST /runs response — returned immediately, before the work starts."""

    run_id: str
    status: Literal["queued"]
    stream_url: str   # convenience for the UI: /runs/{run_id}/stream


class StageProgress(BaseModel):
    """Live progress inside the currently-running stage."""

    stage: PipelineStage
    pct: float = Field(ge=0.0, le=1.0)
    message: str = ""


class RunCounts(BaseModel):
    posts: int = 0
    clusters: int = 0
    personas: int = 0
    journeys: int = 0


class RunSummary(BaseModel):
    """One row in GET /runs."""

    run_id: str
    topic: str
    region: str
    sources: list[str]
    status: RunStatus
    created_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    counts: RunCounts = Field(default_factory=RunCounts)


class RunDetail(RunSummary):
    """GET /runs/{run_id} — adds live progress + the original params."""

    progress: StageProgress | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class DocResponse(BaseModel):
    """GET /runs/{run_id}/doc/{doc_id} — the source quote behind a citation."""

    doc_id: str
    post_id: str
    source: str
    source_category: str = ""   # forums | reviews | qa | blogs | … — for UI icons
    url: str
    title: str | None = None
    body: str
    language: str
    posted_at: datetime | None = None


class OkResponse(BaseModel):
    ok: bool = True
    message: str = ""
