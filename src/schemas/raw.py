from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.schemas.enums import SignalType, SourceCategory


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

    `source_category` and `signal_type` are propagated from the source's
    `SourceConfig` so downstream phases can filter / weight without joining
    back to the registry.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    source_category: SourceCategory
    region: str
    language: str
    url: HttpUrl
    author_hash: str
    title: str | None = None
    body: str
    posted_at: datetime
    signal_type: SignalType
    engagement_metrics: dict[str, int] = Field(default_factory=dict)
    replies: list[Reply] = Field(default_factory=list)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


Thread = RawPost
