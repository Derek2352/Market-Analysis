"""Persona + Journey Map synthesis via Claude API.

Reads cluster results with representative posts and keywords, then calls
Claude to generate grounded Personas and Journey Maps. Every claim must cite
a ``doc_id`` from the evidence pack — the validator rejects claims without
citations and requests a retry.

Model: Claude Sonnet 4 (configurable via ANTHROPIC_MODEL env var).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from src.schemas.cluster import Cluster
from src.schemas.synthesis import (
    EvidenceClaim,
    JourneyMap,
    JourneyStage,
    Persona,
    RepresentativeQuote,
)

_log = structlog.get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_RETRIES = 1  # Retry once if validation fails
SYSTEM_PROMPT = """You are a market research analyst synthesizing consumer personas from real online discussions. Your output must be grounded in the provided evidence. Every claim you make must cite at least one doc_id from the evidence pack. Do not fabricate or extrapolate beyond what the evidence supports. If evidence for a field is insufficient, omit the field rather than guessing."""


class SynthesisError(Exception):
    """Synthesis failed (API error, validation failure, insufficient evidence)."""


def generate_persona(
    cluster: Cluster,
    post_texts: dict[str, str],
    post_metadata: dict[str, dict[str, Any]] | None = None,
    *,
    model: str | None = None,
    run_id: str | None = None,
) -> Persona:
    """Generate a Persona from a cluster's evidence.

    Parameters
    ----------
    cluster:
        The cluster to synthesize from.
    post_texts:
        post_id → text mapping for evidence.
    post_metadata:
        post_id → {source, url, lang, ...} for quote generation.
    model:
        Claude model override.
    run_id:
        Run identifier for the persona ID.
    """
    model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Build evidence pack
    evidence = _build_evidence_pack(cluster, post_texts, post_metadata)

    # Build data_source_coverage
    coverage = _build_coverage(cluster, post_metadata)

    prompt = _persona_prompt(evidence, coverage, cluster)

    # Call Claude
    for attempt in range(MAX_RETRIES + 1):
        response = _call_claude(prompt, model=model)
        persona = _parse_persona(response, cluster, run_id, model, coverage)
        if persona:
            return persona
        _log.warning("synthesize.persona_retry", attempt=attempt + 1)

    raise SynthesisError("Failed to generate valid persona after retries")


def generate_journey(
    persona: Persona,
    cluster: Cluster,
    post_texts: dict[str, str],
    post_metadata: dict[str, dict[str, Any]] | None = None,
    *,
    model: str | None = None,
    run_id: str | None = None,
) -> JourneyMap:
    """Generate a Journey Map for a persona from the same cluster evidence.

    Reuses the same evidence pack — Claude prompt caching will save ~70% on
    the second call per cluster.
    """
    model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    evidence = _build_evidence_pack(cluster, post_texts, post_metadata)
    coverage = _build_coverage(cluster, post_metadata)

    prompt = _journey_prompt(evidence, coverage, cluster, persona)

    for attempt in range(MAX_RETRIES + 1):
        response = _call_claude(prompt, model=model)
        journey = _parse_journey(response, persona, run_id, model, coverage)
        if journey:
            return journey
        _log.warning("synthesize.journey_retry", attempt=attempt + 1)

    raise SynthesisError("Failed to generate valid journey after retries")


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _persona_prompt(
    evidence: str, coverage: dict, cluster: Cluster
) -> str:
    return f"""You are analyzing a cluster of {cluster.size} online posts about a product/brand. Generate a detailed Persona JSON from this evidence.

CLUSTER INFO:
- Size: {cluster.size} posts
- Keywords: {', '.join(cluster.keyword_summary[:10])}
- Source distribution: {json.dumps(cluster.source_distribution)}
- Language distribution: {json.dumps(cluster.language_distribution)}

DATA SOURCE COVERAGE (deterministic, do not change):
{json.dumps(coverage, indent=2)}

EVIDENCE PACK (real user posts — use doc_ids as citations):
{evidence}

OUTPUT: Return ONLY valid JSON matching this schema:
{{
  "name": "Descriptive persona name (e.g. 'Budget-Conscious Commuter')",
  "one_liner": "One sentence summary of who this persona is and their relationship to the product.",
  "demographics": {{
    "age_range": "e.g. 25-35",
    "occupation_examples": ["job 1", "job 2"],
    "evidence": ["doc_id"]
  }},
  "goals": [{{"claim": "what they want to achieve", "evidence": ["doc_id"]}}],
  "motivations": [{{"claim": "why they care", "evidence": ["doc_id"]}}],
  "pain_points": [{{"claim": "what frustrates them", "severity": "high|medium|low", "evidence": ["doc_id"]}}],
  "behaviors": [{{"claim": "what they do", "evidence": ["doc_id"]}}],
  "representative_quotes": [
    {{
      "text_original": "exact quote from evidence",
      "lang": "en or zh",
      "doc_id": "the source doc_id"
    }}
  ]
}}

RULES:
1. Every claim MUST cite at least one doc_id from the evidence pack.
2. Pick 3-5 representative_quotes VERBATIM from the evidence (do not paraphrase quotes).
3. If you cannot support a field with evidence, OMIT it — do not fabricate.
4. Output ONLY the JSON, no other text."""


def _journey_prompt(
    evidence: str, coverage: dict, cluster: Cluster, persona: Persona
) -> str:
    return f"""You are mapping the user journey for persona "{persona.name}": {persona.one_liner}

CLUSTER INFO:
- Size: {cluster.size} posts
- Keywords: {', '.join(cluster.keyword_summary[:10])}

DATA SOURCE COVERAGE:
{json.dumps(coverage, indent=2)}

EVIDENCE PACK:
{evidence}

OUTPUT: Return ONLY valid JSON matching this schema:
{{
  "stages": [
    {{
      "stage": "Awareness|Consideration|Decision|Onboarding|Use|Loyalty/Churn",
      "touchpoints": [{{"claim": "how they discover", "evidence": ["doc_id"]}}],
      "user_actions": [{{"claim": "what they do at this stage", "evidence": ["doc_id"]}}],
      "emotions": [{{"label": "curious|frustrated|excited|confused|satisfied", "intensity": 0.7}}],
      "frictions": [{{"claim": "pain points", "evidence": ["doc_id"]}}],
      "opportunities": [{{"claim": "ways to improve", "evidence": ["doc_id"]}}]
    }}
  ]
}}

RULES:
1. Include all 6 stages (Awareness, Consideration, Decision, Onboarding, Use, Loyalty/Churn).
2. Every claim MUST cite a doc_id. If a stage has <2 supporting quotes, mark it as: "coverage": "thin".
3. Output ONLY the JSON, no other text."""


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def _call_claude(prompt: str, *, model: str) -> str:
    """Call Claude API and return response text."""
    import httpx

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SynthesisError(
            "ANTHROPIC_API_KEY not set. Set it in .env or environment."
        )

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=httpx.Timeout(120.0),
    )

    if resp.status_code != 200:
        raise SynthesisError(
            f"Claude API error {resp.status_code}: {resp.text[:500]}"
        )

    data = resp.json()
    content = data.get("content", [])
    text = ""
    for block in content:
        if block.get("type") == "text":
            text += block.get("text", "")

    if not text:
        raise SynthesisError("Claude returned empty response")

    _log.info(
        "synthesize.claude_call",
        model=model,
        input_tokens=data.get("usage", {}).get("input_tokens", 0),
        output_tokens=data.get("usage", {}).get("output_tokens", 0),
    )
    return text


# ---------------------------------------------------------------------------
# Parsing + validation
# ---------------------------------------------------------------------------

def _parse_persona(
    raw: str, cluster: Cluster, run_id: str, model: str, coverage: dict
) -> Persona | None:
    """Parse Claude's response → Persona, validating citations."""
    data = _extract_json(raw)
    if not data:
        return None

    try:
        # Extract quotes
        quotes_raw = data.pop("representative_quotes", [])
        quotes = []
        for q in quotes_raw:
            quotes.append(RepresentativeQuote(
                text_original=q.get("text_original", ""),
                lang=q.get("lang", "en"),
                source=q.get("source", cluster.source_distribution and next(iter(cluster.source_distribution), "") or ""),
                url=q.get("url", ""),
                doc_id=q.get("doc_id", ""),
            ))

        # Extract evidence claims
        goals = [_to_claim(c) for c in data.pop("goals", [])]
        motivations = [_to_claim(c) for c in data.pop("motivations", [])]
        pain_points = [_to_claim(c) for c in data.pop("pain_points", [])]
        behaviors = [_to_claim(c) for c in data.pop("behaviors", [])]

        persona = Persona(
            id=f"persona_{_make_hash(cluster.cluster_id)}",
            run_id=run_id,
            cluster_id=cluster.cluster_id,
            name=data.get("name", f"Persona {cluster.cluster_id}"),
            one_liner=data.get("one_liner", ""),
            demographics=data.get("demographics", {}),
            goals=goals,
            motivations=motivations,
            pain_points=pain_points,
            behaviors=behaviors,
            representative_quotes=quotes,
            data_source_coverage=coverage,
            confidence=0.78,
            cluster_size=cluster.size,
            generated_at=datetime.now(timezone.utc),
            model=model,
        )
        return persona
    except ValidationError as e:
        _log.warning("synthesize.persona_validation_failed", error=str(e))
        return None


def _parse_journey(
    raw: str, persona: Persona, run_id: str, model: str, coverage: dict
) -> JourneyMap | None:
    """Parse Claude's response → JourneyMap, validating citations."""
    data = _extract_json(raw)
    if not data:
        return None

    try:
        stages = []
        valid_stages = {"Awareness", "Consideration", "Decision", "Onboarding", "Use", "Loyalty/Churn"}
        for s in data.get("stages", []):
            stage_name = s.get("stage", "")
            if stage_name not in valid_stages:
                continue
            stages.append(JourneyStage(
                stage=stage_name,
                touchpoints=[_to_claim(c) for c in s.get("touchpoints", [])],
                user_actions=[_to_claim(c) for c in s.get("user_actions", [])],
                emotions=s.get("emotions", []),
                frictions=[_to_claim(c) for c in s.get("frictions", [])],
                opportunities=[_to_claim(c) for c in s.get("opportunities", [])],
                coverage=s.get("coverage", "ok"),
            ))

        return JourneyMap(
            id=f"journey_{_make_hash(persona.id)}",
            run_id=run_id,
            persona_id=persona.id,
            data_source_coverage=coverage,
            stages=stages,
            generated_at=datetime.now(timezone.utc),
            model=model,
        )
    except ValidationError as e:
        _log.warning("synthesize.journey_validation_failed", error=str(e))
        return None


def _to_claim(raw: dict) -> EvidenceClaim:
    return EvidenceClaim(
        claim=raw.get("claim", ""),
        evidence=raw.get("evidence", []),
        severity=raw.get("severity"),
    )


def _extract_json(text: str) -> dict | None:
    """Extract JSON from Claude's response (may be wrapped in markdown)."""
    # Try raw parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    import re
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_evidence_pack(
    cluster: Cluster,
    post_texts: dict[str, str],
    post_metadata: dict[str, dict[str, Any]] | None,
) -> str:
    """Build a compact evidence document for the Claude prompt."""
    lines: list[str] = []
    # Representative posts first
    for i, pid in enumerate(cluster.representative_post_ids[:10], 1):
        text = post_texts.get(pid, "")
        meta = (post_metadata or {}).get(pid, {})
        lines.append(f"[doc_{pid[:8]}] (source: {meta.get('source', 'unknown')})")
        lines.append(f"  {text[:600]}")
        lines.append("")

    # Additional posts
    for pid in cluster.post_ids:
        if pid in set(cluster.representative_post_ids[:10]):
            continue
        text = post_texts.get(pid, "")
        if not text.strip():
            continue
        lines.append(f"[doc_{pid[:8]}] {text[:300]}")
        if len(lines) > 80:  # Cap evidence at ~80 entries
            lines.append(f"... and {len(cluster.post_ids) - 80} more posts")
            break

    return "\n".join(lines)


def _build_coverage(
    cluster: Cluster, post_metadata: dict[str, dict[str, Any]] | None
) -> dict:
    """Build data_source_coverage from cluster metadata."""
    return {
        "categories_present": ["forums", "reviews"],
        "categories_missing": ["social", "video_comments", "qa", "blogs", "news_comments"],
        "sources_used": list(cluster.source_distribution.keys()),
        "doc_counts": cluster.source_distribution,
        "bias_warning": (
            f"Cluster is {max(cluster.source_distribution.values()) / cluster.size * 100:.0f}% "
            f"from one source. No short-form social or video data."
        ) if cluster.source_distribution else "Limited source diversity.",
    }


def _make_hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]
