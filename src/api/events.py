"""SSE event encoding + per-run event queues.

Each run has an in-memory ``asyncio.Queue`` of events while it's active, and a
``data/runs/{run_id}/events.jsonl`` log on disk. Subscribers replay the
on-disk log first, then tail the in-memory queue until the run reaches a
terminal status.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Sentinel pushed onto a run's queue when the run is finished (succeeded,
# failed, or cancelled). Subscribers stop tailing on receipt.
_CLOSE = object()


@dataclass
class Event:
    """One Server-Sent Event."""

    type: str                       # "queued" | "stage_start" | "progress" | "stage_done" | "done" | "error"
    data: dict[str, Any] = field(default_factory=dict)
    id: int | None = None           # monotonic per-run, set when written to disk

    def encode(self) -> str:
        """Render to the SSE wire format (event + data + id + blank line)."""
        lines = [f"event: {self.type}", f"data: {json.dumps(self.data, ensure_ascii=False, default=str)}"]
        if self.id is not None:
            lines.append(f"id: {self.id}")
        return "\n".join(lines) + "\n\n"


class EventLog:
    """Per-run event log: in-memory queue + on-disk JSONL mirror.

    Mirrored to disk so SSE subscribers can replay the history of a run
    after a server restart, and so ``GET /runs/{run_id}`` works without
    keeping every finished run in memory.
    """

    def __init__(self, run_dir: Path):
        self._dir = run_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "events.jsonl"
        # Each subscriber gets its own queue; emit broadcasts to all of them.
        # This (plus history-id dedup at subscribe time) avoids the
        # "double-yield" bug where one shared queue replays everything that
        # disk history already gave the new subscriber.
        self._subscribers: list[asyncio.Queue[Event | object]] = []
        self._next_id: int = 1
        self._closed: bool = False
        if self._path.exists():
            for raw in self._path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(raw)
                    eid = int(row.get("id", 0))
                    if eid >= self._next_id:
                        self._next_id = eid + 1
                    if row.get("type") in {"done", "error", "cancelled"}:
                        self._closed = True
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue

    @property
    def closed(self) -> bool:
        return self._closed

    def _append_disk(self, event: Event) -> None:
        row = {"id": event.id, "type": event.type, "data": event.data}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _broadcast(self, item: Event | object) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> Event:
        """Append a new event to the log and broadcast to live subscribers.

        Safe to call from the main asyncio loop. Callers in worker threads
        should use ``emit_threadsafe``.
        """
        if self._closed:
            return Event(type=event_type, data=data or {}, id=None)
        event = Event(type=event_type, data=dict(data or {}), id=self._next_id)
        self._next_id += 1
        self._append_disk(event)
        self._broadcast(event)
        if event_type in {"done", "error", "cancelled"}:
            self._closed = True
            self._broadcast(_CLOSE)
        return event

    def emit_threadsafe(
        self, loop: asyncio.AbstractEventLoop, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        """Schedule ``emit`` on the loop from a worker thread."""
        loop.call_soon_threadsafe(self.emit, event_type, data)

    def history(self) -> list[Event]:
        """Replay all events from disk in order."""
        out: list[Event] = []
        if not self._path.exists():
            return out
        for raw in self._path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            out.append(Event(type=row["type"], data=row.get("data", {}), id=row.get("id")))
        return out

    async def subscribe(self):
        """Async generator yielding events.

        Registers a fresh queue first (so we don't miss events fired during
        history replay), snapshots disk history, yields it, then tails the
        queue — skipping any event whose id already appeared in history.
        """
        q: asyncio.Queue[Event | object] = asyncio.Queue()
        self._subscribers.append(q)
        try:
            history = self.history()
            seen_ids = {ev.id for ev in history if ev.id is not None}
            for ev in history:
                yield ev
            if self._closed:
                return
            while True:
                item = await q.get()
                if item is _CLOSE:
                    return
                ev: Event = item  # type: ignore[assignment]
                if ev.id in seen_ids:
                    continue
                seen_ids.add(ev.id)
                yield ev
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)
