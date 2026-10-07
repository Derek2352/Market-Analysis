"""Run-result cache: ``(topic, region, sources, since_days, …)`` → run_id.

Re-scraping every time the UI launcher is reopened wastes time, money,
and bandwidth, especially for repeat lookups against the same product.
This cache remembers the run_id of the last successful run for a given
parameter set; ``POST /runs`` reuses it instead of kicking off the whole
pipeline again.

Design notes:

  - **Key shape.** The key intentionally only covers params that affect
    output: topic-slug, region, sorted sources, since_days, provider,
    limit_per_source. ``max_cost_usd`` and ``force`` are cost-gate
    knobs and don't change what's generated; ``bypass_cache`` is the
    override itself.

  - **TTL.** Default 24h. The right window depends on how fast the
    source corpus drifts; a day is a sensible default for forums + app
    reviews + news. Override via ``RunCache(ttl_seconds=...)``.

  - **Disk.** A single JSON file at ``data/runs/_cache.json``. Keys are
    canonicalised JSON strings so we can store them as object keys.
    Atomic writes (tmp + replace) to survive crashes.

  - **Only successful runs.** Failed / cancelled / in-flight runs never
    enter the cache. Lookup also verifies the recorded run_id still
    exists on disk and that the params match — guards against a
    truncated runs dir or a manual edit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.util_atomic import atomic_write_json

CACHE_FILENAME = "_cache.json"
DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours


@dataclass(frozen=True)
class RunCacheKey:
    """The parameter tuple a cache lookup keys on."""

    topic: str
    region: str
    sources: tuple[str, ...]
    since_days: int
    provider: str
    limit_per_source: int

    @classmethod
    def from_request_dict(cls, params: dict[str, Any]) -> "RunCacheKey":
        """Build a key from a RunRequest's model_dump().

        Normalises the source list (sorted + de-duplicated) so request
        order doesn't fragment the cache.
        """
        topic = (params.get("topic") or "").strip().lower()
        region = (params.get("region") or "").upper()
        srcs = tuple(sorted({s for s in (params.get("sources") or []) if s}))
        return cls(
            topic=topic,
            region=region,
            sources=srcs,
            since_days=int(params.get("since_days", 0)),
            provider=str(params.get("provider", "anthropic")),
            limit_per_source=int(params.get("limit_per_source", 0)),
        )

    def to_storage_key(self) -> str:
        """Canonical string form for use as a dict key on disk."""
        return json.dumps(
            {
                "topic": self.topic,
                "region": self.region,
                "sources": list(self.sources),
                "since_days": self.since_days,
                "provider": self.provider,
                "limit_per_source": self.limit_per_source,
            },
            sort_keys=True,
            ensure_ascii=False,
        )


@dataclass
class CacheEntry:
    run_id: str
    recorded_at: datetime


class RunCache:
    """Disk-backed cache from RunCacheKey to a recent successful run_id."""

    def __init__(
        self,
        runs_root: Path,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self._root = Path(runs_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._path = self._root / CACHE_FILENAME
        self._ttl = timedelta(seconds=ttl_seconds)
        self._entries: dict[str, CacheEntry] = {}
        self._load()

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    # ---- public API --------------------------------------------------

    def lookup(
        self, key: RunCacheKey, *, now: datetime | None = None,
    ) -> str | None:
        """Return the cached run_id if fresh + still on disk, else None."""
        now = now or datetime.now(timezone.utc)
        entry = self._entries.get(key.to_storage_key())
        if entry is None:
            return None
        if now - entry.recorded_at > self._ttl:
            return None
        # Belt-and-braces: the run directory must still exist.
        if not (self._root / entry.run_id / "run.json").exists():
            return None
        return entry.run_id

    def record(
        self, key: RunCacheKey, run_id: str,
        *, now: datetime | None = None,
    ) -> None:
        """Pin a run_id for this key. Overwrites any prior entry."""
        now = now or datetime.now(timezone.utc)
        self._entries[key.to_storage_key()] = CacheEntry(
            run_id=run_id, recorded_at=now,
        )
        self._persist()

    def invalidate(self, key: RunCacheKey) -> None:
        """Drop the entry for this key (no-op if absent)."""
        if self._entries.pop(key.to_storage_key(), None) is not None:
            self._persist()

    def clear(self) -> None:
        """Wipe every entry."""
        if self._entries:
            self._entries.clear()
            self._persist()

    # ---- iteration / introspection -----------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: RunCacheKey) -> bool:
        return key.to_storage_key() in self._entries

    def items(self) -> list[tuple[RunCacheKey, CacheEntry]]:
        """All entries as (key, entry) pairs. Used by tests + diagnostics."""
        out: list[tuple[RunCacheKey, CacheEntry]] = []
        for raw, entry in self._entries.items():
            payload = json.loads(raw)
            out.append((
                RunCacheKey(
                    topic=payload["topic"],
                    region=payload["region"],
                    sources=tuple(payload.get("sources", [])),
                    since_days=int(payload.get("since_days", 0)),
                    provider=payload.get("provider", "anthropic"),
                    limit_per_source=int(payload.get("limit_per_source", 0)),
                ),
                entry,
            ))
        return out

    # ---- persistence -------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            # Cache is best-effort; corruption shouldn't break the API.
            return
        for raw, body in (payload or {}).items():
            try:
                recorded_at = datetime.fromisoformat(body["recorded_at"])
                self._entries[raw] = CacheEntry(
                    run_id=body["run_id"], recorded_at=recorded_at,
                )
            except (KeyError, ValueError, TypeError):
                continue

    def _persist(self) -> None:
        payload = {
            raw: {
                "run_id": entry.run_id,
                "recorded_at": entry.recorded_at.isoformat(),
            }
            for raw, entry in self._entries.items()
        }
        atomic_write_json(self._path, payload)
