"""Per-post metadata maps for clustering: language, sentiment, month.

``cluster_embeddings`` turns these into each cluster's language / sentiment /
temporal distributions. Every caller loads the same raw scrape files, so the
maps are built here once instead of four slightly different ways.

Sentiment is only recorded where it is grounded in the source itself — a star
rating on a review (1-2 negative, 3 neutral, 4-5 positive). Posts without a
rating get no sentiment entry rather than an invented one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PostMaps:
    lang: dict[str, str] = field(default_factory=dict)
    sentiment: dict[str, str] = field(default_factory=dict)
    month: dict[str, str] = field(default_factory=dict)

    def as_kwargs(self) -> dict[str, dict[str, str]]:
        """Keyword arguments for ``cluster_embeddings``."""
        return {
            "lang_map": self.lang,
            "sentiment_map": self.sentiment,
            "temporal_map": self.month,
        }


def _rating_sentiment(rating: Any) -> str | None:
    try:
        r = int(rating)
    except (TypeError, ValueError):
        return None
    if not 1 <= r <= 5:
        return None
    if r <= 2:
        return "negative"
    if r == 3:
        return "neutral"
    return "positive"


def build_post_maps(posts: list[dict[str, Any]]) -> PostMaps:
    """Build the maps from raw post dicts (as written by ``RunWriter``)."""
    maps = PostMaps()
    for p in posts:
        pid = p.get("id")
        if not pid:
            continue
        lang = p.get("language_detected") or p.get("language")
        if lang:
            maps.lang[pid] = lang
        sentiment = _rating_sentiment((p.get("engagement_metrics") or {}).get("rating"))
        if sentiment:
            maps.sentiment[pid] = sentiment
        posted = str(p.get("posted_at") or "")
        if len(posted) >= 7 and posted[4] == "-":
            maps.month[pid] = posted[:7]
    return maps


def load_post_maps(raw_dir: Path) -> PostMaps:
    """Read every scrape file under ``data/raw/<slug>/<region>`` and build maps."""
    posts: list[dict[str, Any]] = []
    if raw_dir.exists():
        for rf in sorted(raw_dir.glob("*.json")):
            if rf.name.endswith("._run.json"):
                continue
            try:
                data = json.loads(rf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, list):
                posts.extend(data)
    return build_post_maps(posts)
