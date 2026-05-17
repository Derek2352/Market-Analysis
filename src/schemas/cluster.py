"""Cluster output schema — produced by the clustering layer.

Used for persistence (JSON/Parquet) and as input to Phase 4 synthesis.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Cluster(BaseModel):
    """A single cluster of related posts."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    topic: str
    region: str
    size: int
    post_ids: list[str]

    # The 5 posts closest to the cluster centroid (anchors for LLM synthesis)
    representative_post_ids: list[str] = Field(default_factory=list)

    # Top 10 keywords via class-based TF-IDF (c-TF-IDF, BERTopic's technique)
    keyword_summary: list[str] = Field(default_factory=list)

    # Source diversity
    source_distribution: dict[str, int] = Field(default_factory=dict)
    language_distribution: dict[str, int] = Field(default_factory=dict)
    sentiment_distribution: dict[str, int] = Field(default_factory=dict)

    # Temporal distribution — posts per month
    temporal_distribution: dict[str, int] = Field(default_factory=dict)

    noise_post_count: int = 0

    generated_at: datetime | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ClusteringResult(BaseModel):
    """Full clustering output for a run."""

    model_config = ConfigDict(extra="forbid")

    topic: str
    region: str
    total_posts: int
    noise_count: int
    clusters: list[Cluster]
    params: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime | None = None
