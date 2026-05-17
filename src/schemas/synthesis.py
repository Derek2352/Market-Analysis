"""Synthesis output schemas — Persona and Journey Map.

Produced by Phase 4's Claude-powered synthesis. Every claim field carries an
``evidence`` array with ``doc_id`` references to stored posts, enforcing the
anti-hallucination contract: no claim without a source.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceClaim(BaseModel):
    """A single claim backed by post evidence."""

    model_config = ConfigDict(extra="forbid")

    claim: str
    evidence: list[str] = Field(default_factory=list)  # doc_id references
    severity: str | None = None  # high / medium / low (for pain points)


class RepresentativeQuote(BaseModel):
    """A verbatim quote from a source post."""

    model_config = ConfigDict(extra="forbid")

    text_original: str
    text_translated: str | None = None
    lang: str = "en"
    source: str
    url: str
    doc_id: str


class Persona(BaseModel):
    """A synthesized persona from a cluster of related opinions."""

    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    cluster_id: str
    name: str
    one_liner: str
    language: str = "en"
    demographics: dict[str, Any] = Field(default_factory=dict)
    goals: list[EvidenceClaim] = Field(default_factory=list)
    motivations: list[EvidenceClaim] = Field(default_factory=list)
    pain_points: list[EvidenceClaim] = Field(default_factory=list)
    preferred_channels: list[EvidenceClaim] = Field(default_factory=list)
    behaviors: list[EvidenceClaim] = Field(default_factory=list)
    representative_quotes: list[RepresentativeQuote] = Field(default_factory=list)
    data_source_coverage: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    cluster_size: int = 0
    generated_at: datetime | None = None
    model: str = ""


class JourneyStage(BaseModel):
    """One stage in the user journey."""

    model_config = ConfigDict(extra="forbid")

    stage: str  # Awareness | Consideration | Decision | Onboarding | Use | Loyalty/Churn
    touchpoints: list[EvidenceClaim] = Field(default_factory=list)
    user_actions: list[EvidenceClaim] = Field(default_factory=list)
    emotions: list[dict[str, Any]] = Field(default_factory=list)
    frictions: list[EvidenceClaim] = Field(default_factory=list)
    opportunities: list[EvidenceClaim] = Field(default_factory=list)
    coverage: str = "ok"  # ok | thin | none


class JourneyMap(BaseModel):
    """A user journey map synthesized from a cluster."""

    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    persona_id: str
    language: str = "en"
    data_source_coverage: dict[str, Any] = Field(default_factory=dict)
    stages: list[JourneyStage] = Field(default_factory=list)
    generated_at: datetime | None = None
    model: str = ""
