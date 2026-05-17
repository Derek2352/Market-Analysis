from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Reply(BaseModel):
    """A single reply within a thread. Source-agnostic."""

    model_config = ConfigDict(extra="forbid")

    id: str
    author_hash: str
    body: str
    posted_at: datetime
    parent_reply_id: str | None = None
    engagement_metrics: dict[str, int] = Field(default_factory=dict)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class RawPost(BaseModel):
    """A scraped post.

    Returned by `SourceScraper.search` (replies may be empty) and by
    `SourceScraper.fetch_thread` (replies populated when available).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    region: str
    language: str
    url: HttpUrl
    author_hash: str
    title: str | None = None
    body: str
    posted_at: datetime
    engagement_metrics: dict[str, int] = Field(default_factory=dict)
    replies: list[Reply] = Field(default_factory=list)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


Thread = RawPost
