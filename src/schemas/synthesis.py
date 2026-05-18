"""Synthesis output schemas — Persona and Journey Map.

Produced by Phase 4's LLM-powered synthesis. Every claim carries an
``evidence`` array of ``doc_id`` references to stored posts, enforcing the
anti-hallucination contract: no claim without a source.

``ClaimList`` wraps every list of claims with a ``coverage`` marker:
  - ``ok``         — claims well-supported by evidence
  - ``unverified`` — the validator dropped some claims after a retry pass;
                     what's left may be partial or empty

``JourneyStage.coverage`` independently records data sparsity at the stage
level: ``thin`` when there are fewer than 2 supporting quotes for a stage,
``none`` when there are zero.
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


class ClaimList(BaseModel):
    """A bucket of claims with a coverage marker.

    The validator can downgrade a bucket from ``ok`` to ``unverified`` when
    it has dropped claims after a retry pass. The remaining claims are still
    grounded — the marker tells the user the bucket is partial.
    """

    model_config = ConfigDict(extra="forbid")

    claims: list[EvidenceClaim] = Field(default_factory=list)
    coverage: str = "ok"  # ok | unverified


class RepresentativeQuote(BaseModel):
    """A verbatim quote from a source post."""

    model_config = ConfigDict(extra="forbid")

    text_original: str
    text_translated: str | None = None
    lang: str = "en"
    source: str
    url: str
    doc_id: str


class EmotionPoint(BaseModel):
    """One emotion data point on a journey stage, with grounding."""

    model_config = ConfigDict(extra="forbid")

    label: str
    intensity: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


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

    goals: ClaimList = Field(default_factory=ClaimList)
    motivations: ClaimList = Field(default_factory=ClaimList)
    pain_points: ClaimList = Field(default_factory=ClaimList)
    preferred_channels: ClaimList = Field(default_factory=ClaimList)
    behaviors: ClaimList = Field(default_factory=ClaimList)

    representative_quotes: list[RepresentativeQuote] = Field(default_factory=list)
    data_source_coverage: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    cluster_size: int = 0
    generated_at: datetime | None = None
    model: str = ""
    provider: str = ""  # "anthropic" | "deepseek"


class JourneyStage(BaseModel):
    """One stage in the user journey."""

    model_config = ConfigDict(extra="forbid")

    stage: str  # Awareness | Consideration | Decision | Onboarding | Use | Loyalty/Churn

    touchpoints: ClaimList = Field(default_factory=ClaimList)
    user_actions: ClaimList = Field(default_factory=ClaimList)
    emotions: list[EmotionPoint] = Field(default_factory=list)
    frictions: ClaimList = Field(default_factory=ClaimList)
    opportunities: ClaimList = Field(default_factory=ClaimList)

    # Stage-level data sparsity marker.
    # ok    — adequate evidence (>= 2 supporting quotes across this stage)
    # thin  — only 1 supporting quote in evidence — kept but flagged
    # none  — no supporting evidence; LLM was told NOT to fabricate
    coverage: str = "ok"


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
    provider: str = ""
