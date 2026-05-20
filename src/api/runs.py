"""Run state store — in-memory dict + on-disk mirror.

A "run" is one execution of the scrape → embed → cluster → synthesize
pipeline for a (topic, region, sources) triple. State lives under
``data/runs/{run_id}/`` so server restarts don't lose history.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.api.events import EventLog
from src.api.models import (
    PipelineStage,
    RunCounts,
    RunDetail,
    RunRequest,
    RunStatus,
    RunSummary,
    StageProgress,
)


def new_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


class RunState:
    """The whole state of one run. Persisted to ``data/runs/{run_id}/run.json``."""

    def __init__(self, run_dir: Path, summary: RunSummary, params: dict[str, Any]):
        self.dir = run_dir
        self.summary = summary
        self.params = params
        self.progress: StageProgress | None = None
        self.events = EventLog(run_dir)

    # ---- transitions ----------------------------------------------------

    def set_status(self, status: RunStatus, *, error: str | None = None) -> None:
        self.summary.status = status
        if error is not None:
            self.summary.error = error
        if status in {"succeeded", "failed", "cancelled"}:
            self.summary.finished_at = datetime.now(timezone.utc)
            self.progress = None
        self._persist()

    def set_progress(self, stage: PipelineStage, pct: float, message: str = "") -> None:
        self.progress = StageProgress(stage=stage, pct=pct, message=message)
        # Don't persist on every tick — too chatty. Re-persisted at stage
        # transitions via set_status / set_counts.

    def set_counts(self, **counts: int) -> None:
        for k, v in counts.items():
            if hasattr(self.summary.counts, k):
                setattr(self.summary.counts, k, v)
        self._persist()

    # ---- serialization --------------------------------------------------

    def to_detail(self) -> RunDetail:
        return RunDetail(
            **self.summary.model_dump(),
            progress=self.progress,
            params=self.params,
        )

    def _persist(self) -> None:
        payload = {
            "summary": self.summary.model_dump(mode="json"),
            "params": self.params,
        }
        path = self.dir / "run.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)

    # ---- factories ------------------------------------------------------

    @classmethod
    def create(cls, runs_root: Path, run_id: str, request: RunRequest) -> "RunState":
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        summary = RunSummary(
            run_id=run_id,
            topic=request.topic,
            region=request.region,
            sources=list(request.sources),
            status="queued",
            created_at=datetime.now(timezone.utc),
            counts=RunCounts(),
        )
        params = request.model_dump()
        state = cls(run_dir, summary, params)
        state._persist()
        state.events.emit("queued", {
            "run_id": run_id,
            "topic": request.topic,
            "region": request.region,
            "sources": list(request.sources),
        })
        return state

    @classmethod
    def load(cls, run_dir: Path) -> "RunState":
        path = run_dir / "run.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        summary = RunSummary(**payload["summary"])
        return cls(run_dir, summary, payload.get("params", {}))


class RunStore:
    """Process-wide store of runs.

    In-memory dict for the currently-active or recently-touched runs;
    disk for the canonical list. ``GET /runs`` enumerates the directory.
    """

    def __init__(self, runs_root: Path):
        self._root = runs_root
        self._root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, RunState] = {}

    @property
    def root(self) -> Path:
        return self._root

    def create(self, request: RunRequest) -> RunState:
        # Generate a unique run id even if two requests arrive in the same second.
        base = new_run_id()
        run_id = base
        suffix = 1
        while (self._root / run_id).exists():
            run_id = f"{base}-{suffix}"
            suffix += 1
        state = RunState.create(self._root, run_id, request)
        self._cache[run_id] = state
        return state

    def get(self, run_id: str) -> RunState | None:
        state = self._cache.get(run_id)
        if state is not None:
            return state
        run_dir = self._root / run_id
        if not (run_dir / "run.json").exists():
            return None
        state = RunState.load(run_dir)
        self._cache[run_id] = state
        return state

    def list(self) -> list[RunSummary]:
        """All known runs, newest first."""
        out: list[RunSummary] = []
        if not self._root.exists():
            return out
        for sub in sorted(self._root.iterdir(), reverse=True):
            if not sub.is_dir() or not (sub / "run.json").exists():
                continue
            try:
                state = self.get(sub.name)
                if state is not None:
                    out.append(state.summary)
            except Exception:
                continue
        return out
