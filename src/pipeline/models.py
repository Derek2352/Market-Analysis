"""Temporal trend analysis — time-bucket posts, detect volume spikes.

Used by the ``--temporal`` flag on ``mkt synthesize`` to produce a
time-series breakdown of post volume and sentiment across the scraped
window.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

_log = structlog.get_logger(__name__)


@dataclass
class TemporalBucket:
    """One time bucket (week or month) with aggregated stats."""

    label: str  # e.g. "2026-W19" or "2026-05"
    start: datetime
    end: datetime
    post_count: int = 0
    # complaint keywords matched (simple heuristic: "bad", "terrible", "expensive", etc.)
    complaint_count: int = 0
    # sentiment score placeholder (-1 to 1, computed via simple keyword heuristic)
    sentiment_score: float = 0.0


@dataclass
class TemporalTrends:
    """Time-bucketed analysis of post volume and complaint trends."""

    topic: str
    region: str
    bucket_type: str = "week"  # "week" or "month"
    buckets: list[TemporalBucket] = field(default_factory=list)

    # Spike detection
    spikes: list[dict[str, Any]] = field(default_factory=list)

    # Summary stats
    total_posts: int = 0
    date_range_start: str = ""
    date_range_end: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "region": self.region,
            "bucket_type": self.bucket_type,
            "total_posts": self.total_posts,
            "date_range_start": self.date_range_start,
            "date_range_end": self.date_range_end,
            "buckets": [
                {
                    "label": b.label,
                    "start": b.start.isoformat(),
                    "end": b.end.isoformat(),
                    "post_count": b.post_count,
                    "complaint_count": b.complaint_count,
                    "sentiment_score": round(b.sentiment_score, 3),
                }
                for b in self.buckets
            ],
            "spikes": self.spikes,
        }


# ── Keyword heuristics (no ML dep for quick sentiment) ──────────────

_COMPLAINT_KEYWORDS = frozenset({
    "bad", "terrible", "awful", "worst", "hate", "broken", "bug",
    "crash", "slow", "expensive", "overpriced", "scam", "useless",
    "rubbish", "garbage", "frustrating", "annoying", "disappointed",
    "難用", "垃圾", "貴", "廢", "唔掂", "好差", "唔得", "仲差過",
    "まずい", "高い", "使えない", "最悪", "クソ",
})


def compute_temporal_trends(
    post_metadata: dict[str, dict[str, Any]],
    *,
    topic: str = "",
    region: str = "",
    bucket_type: str = "week",
) -> TemporalTrends:
    """Bucket posts by *bucket_type* and compute volume/complaint trends.

    Parameters
    ----------
    post_metadata:
        Dict of ``post_id → {source, url, lang, posted_at, ...}`` as built
        by the synthesize CLI command.
    bucket_type:
        ``"week"`` (ISO week) or ``"month"``.
    """
    trends = TemporalTrends(
        topic=topic,
        region=region,
        bucket_type=bucket_type,
    )

    # ── Parse all post dates ──────────────────────────────────────
    dated: list[tuple[datetime, str]] = []  # (posted_dt, body_text)
    for pid, meta in post_metadata.items():
        posted_str = meta.get("posted_at")
        if posted_str is None:
            continue
        try:
            posted_dt = _parse_posted_at(posted_str)
        except (ValueError, TypeError):
            continue
        body = meta.get("body", "")
        dated.append((posted_dt, body))

    if not dated:
        return trends

    dated.sort(key=lambda x: x[0])

    # ── Determine date range ──────────────────────────────────────
    first_dt = dated[0][0]
    last_dt = dated[-1][0]
    trends.date_range_start = first_dt.isoformat()
    trends.date_range_end = last_dt.isoformat()
    trends.total_posts = len(dated)

    # ── Build buckets ─────────────────────────────────────────────
    if bucket_type == "month":
        buckets = _build_monthly_buckets(first_dt, last_dt, dated)
    else:
        buckets = _build_weekly_buckets(first_dt, last_dt, dated)

    trends.buckets = buckets

    # ── Detect spikes ─────────────────────────────────────────────
    trends.spikes = _detect_spikes(buckets)

    return trends


def _build_weekly_buckets(
    first_dt: datetime,
    last_dt: datetime,
    dated: list[tuple[datetime, str]],
) -> list[TemporalBucket]:
    """Build ISO-week buckets from *first_dt* through *last_dt*."""
    buckets: list[TemporalBucket] = []

    # Walk week by week
    current = first_dt - timedelta(days=first_dt.weekday())  # Monday of first week
    end = last_dt

    # Group posts by ISO week
    week_groups: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for dt, body in dated:
        iso_year, iso_week, _ = dt.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        week_groups[key].append((dt, body))

    for key in sorted(week_groups):
        items = week_groups[key]
        start = items[0][0]
        end_dt = items[-1][0]
        label = key

        post_count = len(items)
        complaint_count = sum(
            1 for _, body in items
            if any(kw in body.lower() for kw in _COMPLAINT_KEYWORDS)
        )
        sentiment = (
            -complaint_count / post_count if post_count > 0 else 0.0
        )

        buckets.append(TemporalBucket(
            label=label,
            start=start,
            end=end_dt,
            post_count=post_count,
            complaint_count=complaint_count,
            sentiment_score=round(sentiment, 3),
        ))

    return buckets


def _build_monthly_buckets(
    first_dt: datetime,
    last_dt: datetime,
    dated: list[tuple[datetime, str]],
) -> list[TemporalBucket]:
    """Build monthly buckets."""
    buckets: list[TemporalBucket] = []

    month_groups: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for dt, body in dated:
        key = dt.strftime("%Y-%m")
        month_groups[key].append((dt, body))

    for key in sorted(month_groups):
        items = month_groups[key]
        start = items[0][0]
        end_dt = items[-1][0]

        post_count = len(items)
        complaint_count = sum(
            1 for _, body in items
            if any(kw in body.lower() for kw in _COMPLAINT_KEYWORDS)
        )
        sentiment = (
            -complaint_count / post_count if post_count > 0 else 0.0
        )

        buckets.append(TemporalBucket(
            label=key,
            start=start,
            end=end_dt,
            post_count=post_count,
            complaint_count=complaint_count,
            sentiment_score=round(sentiment, 3),
        ))

    return buckets


def _detect_spikes(buckets: list[TemporalBucket]) -> list[dict[str, Any]]:
    """Detect volume/complaint spikes (2x the running median)."""
    if len(buckets) < 3:
        return []

    counts = sorted(b.post_count for b in buckets)
    median_idx = len(counts) // 2
    median = counts[median_idx] if len(counts) % 2 == 1 else (
        (counts[median_idx - 1] + counts[median_idx]) / 2
    )

    threshold = max(median * 2, 3)  # at least 3 posts to count as spike
    spikes: list[dict[str, Any]] = []
    for b in buckets:
        if b.post_count >= threshold:
            spikes.append({
                "bucket": b.label,
                "post_count": b.post_count,
                "median": round(median, 1),
                "complaint_count": b.complaint_count,
            })

    return spikes


def _parse_posted_at(value: Any) -> datetime:
    """Parse ``posted_at`` from str or datetime. Mirrors synthesize.py."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        s = value.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    raise ValueError(f"Cannot parse posted_at: {value!r}")
