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
    language_detected: str | None = None
    parent_reply_id: str | None = None
    engagement_metrics: dict[str, int] = Field(default_factory=dict)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class RawPost(BaseModel):
    """A scraped post.

    Returned by `SourceScraper.search` (replies may be empty) and by
    `SourceScraper.fetch_thread` (replies populated when available).

    `language` is the source's declared/primary language (carried from
    `SourceConfig.language`). `language_detected` is the per-post detection
    result — a single source can emit multiple languages (HK App Store mixes
    zh-Hant, en, and code-switched Cantonese-in-Chinese), so downstream
    phases should prefer `language_detected` for clustering and language
    pipelines.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    source_category: SourceCategory
    region: str
    language: str
    language_detected: str | None = None
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
