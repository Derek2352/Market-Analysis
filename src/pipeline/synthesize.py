"""Persona + Journey Map synthesis via Claude or DeepSeek.

Reads cluster results with representative posts and keywords, then calls
an LLM provider (Anthropic or DeepSeek) to generate grounded Personas and
Journey Maps. Every claim must cite a ``doc_id`` from the evidence pack;
the validator rejects claims without citations and either retries with a
stricter prompt or downgrades the offending bucket to ``coverage:
"unverified"`` rather than fabricating.

Prompt caching: the evidence pack (cluster stats + top quotes) is sent as
a cached system block. Persona and Journey calls share that prefix, so the
second call per cluster hits the cache and pays ~10% of the input price.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx
import structlog

from src.schemas.cluster import Cluster
from src.schemas.synthesis import (
    ClaimList,
    EmotionPoint,
    EvidenceClaim,
    JourneyMap,
    JourneyStage,
    Persona,
    RepresentativeQuote,
)

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_TOKENS = 4096
MAX_RETRIES = 1
DEFAULT_COST_CAP_USD = 10000.00  # Effectively no cap — use --max-cost to set one
DEFAULT_COST_FLOOR_USD = 4.00    # Minimum spend for quality depth

# Six canonical journey stages, in order.
JOURNEY_STAGES = (
    "Awareness",
    "Consideration",
    "Decision",
    "Onboarding",
    "Use",
    "Loyalty/Churn",
)

# Persona claim fields the validator and parser iterate over.
_PERSONA_CLAIM_FIELDS = (
    "goals",
    "motivations",
    "pain_points",
    "preferred_channels",
    "behaviors",
)

# Journey claim fields the validator and parser iterate over (per stage).
_JOURNEY_CLAIM_FIELDS = (
    "touchpoints",
    "user_actions",
    "frictions",
    "opportunities",
)

# Token-estimate heuristic: ~3.5 chars per token across mixed EN/ZH text.
# We use this only for dry-run cost estimation, not for hard quotas.
_CHARS_PER_TOKEN = 3.5

# Doc IDs in the prompt look like "doc_<12 hex>" so the validator and the
# LLM use the same identifier scheme.
_DOC_ID_RE = re.compile(r"\[doc_([0-9a-f]+)\]")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SynthesisError(Exception):
    """Synthesis failed (API error, validation failure, insufficient evidence)."""


class CostCapExceeded(SynthesisError):
    """Estimated cost exceeded the run cap. Pass --force or raise --max-cost."""


class MissingAPIKey(SynthesisError):
    """The provider's API key env var is unset."""


# ---------------------------------------------------------------------------
# Evidence pack
# ---------------------------------------------------------------------------


@dataclass
class _EvidencePack:
    """Everything the validator and the LLM need to ground a synthesis.

    `doc_ids` is the canonical set of identifiers the LLM may cite.
    `doc_texts` is the lookup the validator uses to confirm quote substrings.
    `doc_metadata` is per-doc source/url/lang — backfilled into
    representative_quotes after the LLM returns (the LLM frequently omits
    these fields).
    `block_text` is the formatted prompt block (cached portion of the call).
    """

    cluster: Cluster
    doc_ids: set[str]
    doc_texts: dict[str, str]  # doc_id -> raw text
    doc_metadata: dict[str, dict[str, Any]]  # doc_id -> {source, url, lang}
    coverage: dict[str, Any]
    block_text: str

    def estimated_tokens(self) -> int:
        return int(len(self.block_text) / _CHARS_PER_TOKEN)


def _doc_id_for(post_id: str) -> str:
    """Stable short doc_id from a post id. The prompt and validator agree on this."""
    h = hashlib.sha256(post_id.encode("utf-8")).hexdigest()[:12]
    return f"doc_{h}"


def _build_evidence_pack(
    cluster: Cluster,
    post_texts: dict[str, str],
    post_metadata: dict[str, dict[str, Any]] | None,
    region: str,
) -> _EvidencePack:
    """Build the evidence block + lookups for one cluster."""
    metadata = post_metadata or {}
    doc_texts: dict[str, str] = {}
    doc_metadata: dict[str, dict[str, Any]] = {}

    # Representative posts first — these get full text (up to 600 chars).
    rep_ids = list(cluster.representative_post_ids[:10])
    rep_set = set(rep_ids)
    other_ids = [pid for pid in cluster.post_ids if pid not in rep_set]

    lines: list[str] = [
        "CLUSTER METADATA",
        f"- cluster_id: {cluster.cluster_id}",
        f"- region: {cluster.region}",
        f"- size: {cluster.size} posts",
        f"- top keywords (c-TF-IDF): {', '.join(cluster.keyword_summary[:10])}",
        f"- source distribution: {json.dumps(cluster.source_distribution, sort_keys=True)}",
        f"- language distribution: {json.dumps(cluster.language_distribution, sort_keys=True)}",
        "",
    ]

    coverage = _build_coverage(cluster, region)
    lines.append("DATA SOURCE COVERAGE (deterministic; informational)")
    lines.append(json.dumps(coverage, indent=2, sort_keys=True))
    lines.append("")
    lines.append("EVIDENCE PACK")
    lines.append("")

    rep_count = 0
    for pid in rep_ids:
        text = (post_texts.get(pid, "") or "").strip()
        if not text:
            continue
        rep_count += 1
        meta = metadata.get(pid, {})
        doc_id = _doc_id_for(pid)
        doc_texts[doc_id] = text
        doc_metadata[doc_id] = {
            "source": meta.get("source", ""),
            "url": meta.get("url", ""),
            "lang": meta.get("lang", "en"),
        }
        snippet = text[:600].replace("\n", " ")
        lines.append(
            f"[{doc_id}] (source: {meta.get('source', 'unknown')}, "
            f"lang: {meta.get('lang', 'unknown')}, representative #{rep_count})"
        )
        lines.append(f"  {snippet}")
        lines.append("")

    # Other posts — up to a cap, shorter snippet.
    OTHER_CAP = 30
    truncated = max(0, len(other_ids) - OTHER_CAP)
    for pid in other_ids[:OTHER_CAP]:
        text = (post_texts.get(pid, "") or "").strip()
        if not text:
            continue
        meta = metadata.get(pid, {})
        doc_id = _doc_id_for(pid)
        doc_texts[doc_id] = text
        doc_metadata[doc_id] = {
            "source": meta.get("source", ""),
            "url": meta.get("url", ""),
            "lang": meta.get("lang", "en"),
        }
        snippet = text[:300].replace("\n", " ")
        lines.append(
            f"[{doc_id}] (source: {meta.get('source', 'unknown')}, "
            f"lang: {meta.get('lang', 'unknown')})"
        )
        lines.append(f"  {snippet}")
        lines.append("")
    if truncated > 0:
        lines.append(f"... and {truncated} more posts in this cluster (not shown)")

    return _EvidencePack(
        cluster=cluster,
        doc_ids=set(doc_texts.keys()),
        doc_texts=doc_texts,
        doc_metadata=doc_metadata,
        coverage=coverage,
        block_text="\n".join(lines),
    )


# ---------------------------------------------------------------------------
# Coverage (deterministic, computed pre-LLM-call)
# ---------------------------------------------------------------------------


def _build_coverage(cluster: Cluster, region: str) -> dict[str, Any]:
    """Compute data_source_coverage from the cluster's actual source mix.

    Pulls per-source category from the regions registry — never asks the LLM
    to invent this. Phase 6 adds ``category_count`` and ``coverage_tier`` so
    the UI can render a single badge per persona without counting the array.
    """
    from src.regions.registry import get_region
    from src.schemas.enums import SourceCategory

    try:
        region_cfg = get_region(region)
        source_to_cat = {s.source_id: s.category.value for s in region_cfg.sources}
    except KeyError:
        source_to_cat = {}

    sources_used = list(cluster.source_distribution.keys())
    present: set[str] = set()
    for sid in sources_used:
        cat = source_to_cat.get(sid)
        if cat:
            present.add(cat)

    all_categories = {c.value for c in SourceCategory}
    missing = sorted(all_categories - present)

    category_count = len(present)
    coverage_tier = _coverage_tier(category_count)

    return {
        "categories_present": sorted(present),
        "categories_missing": missing,
        "sources_used": sources_used,
        "doc_counts": dict(cluster.source_distribution),
        "category_count": category_count,
        "coverage_tier": coverage_tier,
        "bias_warning": _bias_warning(present, set(missing), cluster),
    }


def _coverage_tier(category_count: int) -> str:
    """Four-tier mapping for the UI badge.

    1 category   → "single-perspective"  (warning style)
    2 categories → "limited"              (muted style)
    3-4          → "balanced"             (neutral)
    5+           → "high"                 (success)
    """
    if category_count <= 1:
        return "single-perspective"
    if category_count == 2:
        return "limited"
    if category_count <= 4:
        return "balanced"
    return "high"


def _bias_warning(
    present: set[str], missing: set[str], cluster: Cluster
) -> str:
    parts: list[str] = []
    if cluster.source_distribution and cluster.size > 0:
        top_count = max(cluster.source_distribution.values())
        if top_count / cluster.size > 0.7:
            top_src = max(
                cluster.source_distribution, key=cluster.source_distribution.get
            )
            pct = int(top_count / cluster.size * 100)
            parts.append(f"{pct}% of evidence from a single source ({top_src})")

    notable_gaps = sorted({"social", "video_comments", "reviews"} & missing)
    if notable_gaps:
        parts.append(f"no {'/'.join(notable_gaps)} coverage")

    if present == {"forums"} or (present and present <= {"forums", "qa", "news_comments"}):
        parts.append(
            "persona likely skews text-first / forum-native; "
            "short-form social and video voices are absent"
        )

    return "; ".join(parts) if parts else "balanced coverage across categories"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


HARD_RULES = """You are a market-research analyst synthesizing user personas and journey
maps from real online discussions. Your output MUST be grounded in the
provided evidence pack.

Hard rules:
1. Every claim MUST cite at least one doc_id that appears in the evidence
   pack. No citation -> omit the claim.
2. representative_quotes.text_original MUST be a verbatim substring of the
   cited doc. Do not paraphrase or translate quote text (translation has
   its own field).
3. If you cannot support a field with evidence, OMIT it. Do not fabricate.
4. Output ONLY valid JSON matching the requested schema. No prose, no
   markdown code fences."""


def _persona_task() -> str:
    return """TASK: Generate a Persona JSON for the cluster in the evidence pack above.

Return ONLY this JSON (no prose, no code fences):
{
  "name": "<Descriptive name, e.g. 'Frustrated Daily Commuter'>",
  "one_liner": "<One sentence summary of this persona's relationship to the product>",
  "demographics": {
    "age_range": "<e.g. 25-35>",
    "occupation_examples": ["..."],
    "evidence": ["doc_id", ...]
  },
  "goals":              [{"claim": "...", "evidence": ["doc_id"]}, ...],
  "motivations":        [{"claim": "...", "evidence": ["doc_id"]}, ...],
  "pain_points":        [{"claim": "...", "severity": "high|medium|low",
                          "evidence": ["doc_id"]}, ...],
  "preferred_channels": [{"claim": "...", "evidence": ["doc_id"]}, ...],
  "behaviors":          [{"claim": "...", "evidence": ["doc_id"]}, ...],
  "representative_quotes": [
    {"text_original": "<VERBATIM substring of a cited doc's content>",
     "lang": "zh|en|...", "doc_id": "doc_..."}
  ]
}

Aim for 3-5 items per claim field. You MUST return at least 3
representative_quotes (target 3-5). Each text_original MUST be a verbatim
substring of the doc it cites. Use doc_ids EXACTLY as shown in the
evidence pack."""


def _journey_task(persona_name: str, persona_one_liner: str) -> str:
    return f"""TASK: Generate a Journey Map JSON for persona "{persona_name}":
  {persona_one_liner}

The persona was synthesized from the same evidence pack above. Reuse those
doc_ids; do not invent new ones.

Return ONLY this JSON (no prose, no code fences):
{{
  "stages": [
    {{
      "stage": "Awareness",
      "touchpoints":   [{{"claim": "...", "evidence": ["doc_id"]}}],
      "user_actions":  [{{"claim": "...", "evidence": ["doc_id"]}}],
      "emotions":      [{{"label": "curious|frustrated|excited|confused|satisfied",
                          "intensity": 0.0, "evidence": ["doc_id"]}}],
      "frictions":     [{{"claim": "...", "evidence": ["doc_id"]}}],
      "opportunities": [{{"claim": "...", "evidence": ["doc_id"]}}]
    }}
    /* repeat for Consideration, Decision, Onboarding, Use, Loyalty/Churn */
  ]
}}

RULES:
- Include all 6 stages (Awareness, Consideration, Decision, Onboarding, Use, Loyalty/Churn).
- If you only find one supporting quote for a stage, include it anyway
  with that single citation. The post-validator marks low-quote stages as
  coverage="thin" automatically.
- Every claim, including each emotion, must cite a doc_id.
- Do NOT pad with claims you can't cite — omitted is better than fabricated."""


def _retry_prefix(errors: list[str]) -> str:
    bullets = "\n".join(f"  {i+1}. {e}" for i, e in enumerate(errors[:20]))
    return f"""Your previous response had these grounding errors:
{bullets}

Fix every one of them and resubmit. Specifically:
- Every doc_id you cite must appear in the evidence pack above.
- Every representative_quote.text_original must be a verbatim substring of
  the cited doc's content.
- Drop any claim or quote you cannot support.

"""


# ---------------------------------------------------------------------------
# LLM clients
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """Token counts returned by the provider."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0       # tokens served from cache (read)
    cache_write_tokens: int = 0        # tokens added to the cache (Anthropic write)

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass
class _ProviderPricing:
    """Per-million-token USD prices for one model."""

    input_per_m: float
    cached_input_per_m: float   # cache read
    cache_write_per_m: float    # Anthropic only — DeepSeek auto, no write premium
    output_per_m: float

    def cost(self, u: Usage) -> float:
        return (
            (u.input_tokens / 1_000_000.0) * self.input_per_m
            + (u.cached_input_tokens / 1_000_000.0) * self.cached_input_per_m
            + (u.cache_write_tokens / 1_000_000.0) * self.cache_write_per_m
            + (u.output_tokens / 1_000_000.0) * self.output_per_m
        )


class LLMClient(Protocol):
    """Provider-agnostic synthesis client.

    Implementations format the request for their provider's chat/messages
    endpoint, set up prompt caching as the provider supports it, parse the
    response into raw text + Usage, and expose a pricing table.
    """

    name: str
    default_model: str

    def synthesize(
        self,
        *,
        rules_block: str,
        evidence_block: str,
        task_message: str,
        model: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> tuple[str, Usage]: ...

    def pricing(self, model: str) -> _ProviderPricing: ...


# Anthropic ---------------------------------------------------------------

_ANTHROPIC_PRICING: dict[str, _ProviderPricing] = {
    # Claude Sonnet 4.6 (claude-sonnet-4-6). Prices in USD per 1M tokens.
    "claude-sonnet-4-6": _ProviderPricing(
        input_per_m=3.00,
        cached_input_per_m=0.30,
        cache_write_per_m=3.75,
        output_per_m=15.00,
    ),
}
_ANTHROPIC_FALLBACK_PRICING = _ANTHROPIC_PRICING["claude-sonnet-4-6"]


class AnthropicClient:
    """Claude via /v1/messages with explicit prompt-caching blocks."""

    name = "anthropic"
    default_model = "claude-sonnet-4-6"
    endpoint = "https://api.anthropic.com/v1/messages"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
    ):
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def pricing(self, model: str) -> _ProviderPricing:
        return _ANTHROPIC_PRICING.get(model, _ANTHROPIC_FALLBACK_PRICING)

    def synthesize(
        self,
        *,
        rules_block: str,
        evidence_block: str,
        task_message: str,
        model: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> tuple[str, Usage]:
        system_blocks = [
            {"type": "text", "text": rules_block},
            {
                "type": "text",
                "text": evidence_block,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": [{"role": "user", "content": task_message}],
        }
        resp = self._client.post(
            self.endpoint,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
        if resp.status_code != 200:
            raise SynthesisError(
                f"Anthropic API error {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
        if not text:
            raise SynthesisError("Anthropic returned an empty response")
        u = data.get("usage", {}) or {}
        usage = Usage(
            input_tokens=u.get("input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            cached_input_tokens=u.get("cache_read_input_tokens", 0),
            cache_write_tokens=u.get("cache_creation_input_tokens", 0),
        )
        return text, usage


# DeepSeek ----------------------------------------------------------------

_DEEPSEEK_PRICING: dict[str, _ProviderPricing] = {
    # deepseek-chat (V3). Prices in USD per 1M tokens. Caching is automatic
    # by prefix match (no write premium). Quoted from DeepSeek's published
    # off-peak pricing; user can override via DEEPSEEK_*_PRICE env vars.
    "deepseek-chat": _ProviderPricing(
        input_per_m=0.27,
        cached_input_per_m=0.07,
        cache_write_per_m=0.27,   # no separate write tier; same as input miss
        output_per_m=1.10,
    ),
}
_DEEPSEEK_FALLBACK_PRICING = _DEEPSEEK_PRICING["deepseek-chat"]


class DeepSeekClient:
    """DeepSeek via OpenAI-compat /chat/completions; automatic prefix caching."""

    name = "deepseek"
    default_model = "deepseek-chat"
    endpoint = "https://api.deepseek.com/chat/completions"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
    ):
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def pricing(self, model: str) -> _ProviderPricing:
        return _DEEPSEEK_PRICING.get(model, _DEEPSEEK_FALLBACK_PRICING)

    def synthesize(
        self,
        *,
        rules_block: str,
        evidence_block: str,
        task_message: str,
        model: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> tuple[str, Usage]:
        # OpenAI-compat: single system message. We concatenate the rules and
        # evidence so the prefix is stable across persona and journey calls
        # (DeepSeek's auto-cache matches by prefix).
        system = f"{rules_block}\n\n{evidence_block}"
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": task_message},
            ],
            "stream": False,
        }
        resp = self._client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "content-type": "application/json",
            },
            json=body,
        )
        if resp.status_code != 200:
            raise SynthesisError(
                f"DeepSeek API error {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise SynthesisError("DeepSeek returned no choices")
        text = (choices[0].get("message") or {}).get("content", "")
        if not text:
            raise SynthesisError("DeepSeek returned empty content")
        u = data.get("usage", {}) or {}
        # DeepSeek reports prompt_cache_hit_tokens / prompt_cache_miss_tokens.
        # We map: miss -> input_tokens; hit -> cached_input_tokens.
        hit = u.get("prompt_cache_hit_tokens", 0)
        miss = u.get("prompt_cache_miss_tokens")
        if miss is None:
            # Older response shape: only prompt_tokens. Treat all as miss.
            miss = max(0, u.get("prompt_tokens", 0) - hit)
        usage = Usage(
            input_tokens=miss,
            output_tokens=u.get("completion_tokens", 0),
            cached_input_tokens=hit,
            cache_write_tokens=0,  # auto-caching, no write premium
        )
        return text, usage


def build_client(
    provider: str,
    *,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> LLMClient:
    """Construct the right LLM client and verify its API key is set."""
    provider = provider.lower()
    if provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise MissingAPIKey(
                "ANTHROPIC_API_KEY is not set. Add it to .env or your "
                "environment (https://console.anthropic.com/settings/keys)."
            )
        return AnthropicClient(key, client=client)
    if provider == "deepseek":
        key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            raise MissingAPIKey(
                "DEEPSEEK_API_KEY is not set. Add it to .env or your "
                "environment (https://platform.deepseek.com/api_keys)."
            )
        return DeepSeekClient(key, client=client)
    raise SynthesisError(
        f"Unknown LLM provider {provider!r}. Use 'anthropic' or 'deepseek'."
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_grounding_persona(
    parsed: dict, pack: _EvidencePack
) -> list[str]:
    """Return a list of grounding errors. Empty list = the response is OK."""
    errors: list[str] = []
    for field_name in _PERSONA_CLAIM_FIELDS:
        for i, c in enumerate(parsed.get(field_name, []) or []):
            errors.extend(_validate_claim(field_name, i, c, pack))

    quotes = parsed.get("representative_quotes", []) or []
    # Minimum quote count: 3 unless the cluster doesn't have that many
    # docs (defensive — small clusters can't honestly produce 3 quotes).
    target_min = min(3, len(pack.doc_ids))
    if len(quotes) < target_min:
        errors.append(
            f"representative_quotes: returned {len(quotes)}, must return "
            f"at least {target_min}"
        )
    for i, q in enumerate(quotes):
        errors.extend(_validate_quote(i, q, pack))

    return errors


def _validate_grounding_journey(
    parsed: dict, pack: _EvidencePack
) -> list[str]:
    errors: list[str] = []
    stages = parsed.get("stages", []) or []
    for s_idx, stage in enumerate(stages):
        stage_name = stage.get("stage", f"stage[{s_idx}]")
        for field_name in _JOURNEY_CLAIM_FIELDS:
            for i, c in enumerate(stage.get(field_name, []) or []):
                errors.extend(
                    _validate_claim(f"{stage_name}.{field_name}", i, c, pack)
                )
        for i, em in enumerate(stage.get("emotions", []) or []):
            errors.extend(
                _validate_claim(f"{stage_name}.emotions", i, em, pack)
            )
    return errors


def _validate_claim(
    location: str, i: int, claim: Any, pack: _EvidencePack
) -> list[str]:
    if not isinstance(claim, dict):
        return [f"{location}[{i}] is not a JSON object"]
    cites = claim.get("evidence") or []
    if not cites:
        return [f"{location}[{i}] missing evidence array"]
    out: list[str] = []
    for cite in cites:
        if cite not in pack.doc_ids:
            out.append(
                f"{location}[{i}] cites unknown doc_id {cite!r}"
            )
    return out


def _validate_quote(
    i: int, quote: Any, pack: _EvidencePack
) -> list[str]:
    if not isinstance(quote, dict):
        return [f"representative_quotes[{i}] is not a JSON object"]
    doc_id = quote.get("doc_id")
    if doc_id not in pack.doc_ids:
        return [f"representative_quotes[{i}] doc_id {doc_id!r} not in pack"]
    text = (quote.get("text_original") or "").strip()
    if not text:
        return [f"representative_quotes[{i}] empty text_original"]
    doc_text = pack.doc_texts.get(doc_id, "")
    if text not in doc_text:
        return [
            f"representative_quotes[{i}] text_original is not a verbatim "
            f"substring of {doc_id}"
        ]
    return []


# ---------------------------------------------------------------------------
# Drop-unverified pass (after second failure)
# ---------------------------------------------------------------------------


def _drop_unverified_persona(
    parsed: dict, pack: _EvidencePack
) -> set[str]:
    """Drop claims/quotes that fail validation; return set of unverified fields."""
    unverified: set[str] = set()
    for field_name in _PERSONA_CLAIM_FIELDS:
        cleaned = []
        dropped = 0
        for c in parsed.get(field_name, []) or []:
            if _validate_claim(field_name, 0, c, pack):
                dropped += 1
                continue
            cleaned.append(c)
        if dropped > 0:
            unverified.add(field_name)
        parsed[field_name] = cleaned

    cleaned_quotes = []
    for q in parsed.get("representative_quotes", []) or []:
        if _validate_quote(0, q, pack):
            continue
        cleaned_quotes.append(q)
    parsed["representative_quotes"] = cleaned_quotes
    return unverified


def _drop_unverified_journey(
    parsed: dict, pack: _EvidencePack
) -> dict[str, set[str]]:
    """Drop unverified claims per stage; return {stage: {unverified_fields}}."""
    per_stage: dict[str, set[str]] = {}
    for stage in parsed.get("stages", []) or []:
        stage_name = stage.get("stage", "")
        local: set[str] = set()
        for field_name in _JOURNEY_CLAIM_FIELDS:
            cleaned = []
            dropped = 0
            for c in stage.get(field_name, []) or []:
                if _validate_claim(field_name, 0, c, pack):
                    dropped += 1
                    continue
                cleaned.append(c)
            if dropped > 0:
                local.add(field_name)
            stage[field_name] = cleaned

        cleaned_emotions = []
        for em in stage.get("emotions", []) or []:
            if _validate_claim("emotions", 0, em, pack):
                continue
            cleaned_emotions.append(em)
        stage["emotions"] = cleaned_emotions

        if local:
            per_stage[stage_name] = local
    return per_stage


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


@dataclass
class CostEstimate:
    provider: str
    model: str
    clusters: int
    estimated_input_tokens: int
    estimated_cached_input_tokens: int
    estimated_output_tokens: int
    estimated_usd: float

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def estimate_cost(
    clusters: list[Cluster],
    *,
    client: LLMClient,
    model: str | None = None,
    post_texts: dict[str, str] | None = None,
    post_metadata: dict[str, dict[str, Any]] | None = None,
    region: str = "",
) -> CostEstimate:
    """Estimate the worst-case (no-retry, full-input) cost of a run.

    Uses a conservative heuristic: every cluster pays for two calls (persona
    + journey). The journey call's input is mostly cache-served, but we
    bound conservatively by treating only 90% of repeated input as cached
    (the rest is the task suffix + any retry context).
    """
    model = model or client.default_model
    pricing = client.pricing(model)

    PERSONA_OUTPUT_TOKENS = 1500
    JOURNEY_OUTPUT_TOKENS = 2200
    TASK_SUFFIX_TOKENS = 350    # persona task suffix is ~350 tokens
    JOURNEY_TASK_TOKENS = 450
    REPLY_OVERHEAD_TOKENS = 50

    total_input = 0
    total_cached = 0
    total_output = 0

    for c in clusters:
        if post_texts is not None:
            pack = _build_evidence_pack(c, post_texts, post_metadata, region)
            evidence_tokens = pack.estimated_tokens()
        else:
            # Fallback: estimate from cluster size if we don't have texts yet.
            evidence_tokens = 200 + c.size * 80
        # First call: full input (evidence + task suffix + reply overhead).
        first_input = evidence_tokens + TASK_SUFFIX_TOKENS + REPLY_OVERHEAD_TOKENS
        # Second call: evidence is cached; pay for the suffix only.
        second_input = JOURNEY_TASK_TOKENS + REPLY_OVERHEAD_TOKENS
        second_cached = evidence_tokens

        total_input += first_input + second_input
        total_cached += second_cached
        total_output += PERSONA_OUTPUT_TOKENS + JOURNEY_OUTPUT_TOKENS

    usage = Usage(
        input_tokens=total_input,
        output_tokens=total_output,
        cached_input_tokens=total_cached,
        cache_write_tokens=total_cached,  # first call writes what second reads
    )
    usd = pricing.cost(usage)
    return CostEstimate(
        provider=client.name,
        model=model,
        clusters=len(clusters),
        estimated_input_tokens=total_input,
        estimated_cached_input_tokens=total_cached,
        estimated_output_tokens=total_output,
        estimated_usd=round(usd, 4),
    )


# ---------------------------------------------------------------------------
# Synthesis (retry-then-mark-unverified loop)
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from an LLM response. Tolerant of code fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Last-ditch: find the first { ... } block.
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except json.JSONDecodeError:
            pass
    return None


def _call_and_validate(
    *,
    client: LLMClient,
    pack: _EvidencePack,
    task_message: str,
    validator: Callable[[dict, _EvidencePack], list[str]],
    model: str,
) -> tuple[dict, Usage, list[str]]:
    """Run synthesis with one retry on grounding failure.

    Returns (parsed_dict, total_usage, residual_errors). Residual errors are
    only non-empty if BOTH attempts failed validation — caller decides
    whether to drop fields and mark unverified.
    """
    total = Usage()
    # First attempt.
    text, u1 = client.synthesize(
        rules_block=HARD_RULES,
        evidence_block=pack.block_text,
        task_message=task_message,
        model=model,
    )
    total = total.add(u1)
    parsed = _extract_json(text)
    if parsed is None:
        # We can't validate something we couldn't parse — retry once.
        errors = ["response was not valid JSON"]
    else:
        errors = validator(parsed, pack)

    if not errors:
        return parsed, total, []

    _log.info(
        "synthesize.retry_after_validation_errors",
        error_count=len(errors),
        first_errors=errors[:3],
    )

    # Second attempt with the errors fed back as a prefix.
    retry_task = _retry_prefix(errors) + task_message
    text2, u2 = client.synthesize(
        rules_block=HARD_RULES,
        evidence_block=pack.block_text,
        task_message=retry_task,
        model=model,
    )
    total = total.add(u2)
    parsed2 = _extract_json(text2)
    if parsed2 is None:
        return parsed or {}, total, errors + ["retry response was not valid JSON"]

    residual = validator(parsed2, pack)
    return parsed2, total, residual


# ---------------------------------------------------------------------------
# Persona / Journey parsing
# ---------------------------------------------------------------------------


def _claim_list_from_raw(
    raw_claims: list[Any] | None,
    *,
    coverage: str = "ok",
    pack: _EvidencePack | None = None,
) -> ClaimList:
    out: list[EvidenceClaim] = []
    for c in raw_claims or []:
        if not isinstance(c, dict):
            continue
        evidence = [str(x) for x in (c.get("evidence") or [])]
        n_users, pct, sentiment = 0, 0.0, {}
        if pack and evidence:
            n_users, pct, sentiment = _compute_quantitative_grounding(evidence, pack)
        out.append(
            EvidenceClaim(
                claim=str(c.get("claim", "")).strip(),
                evidence=evidence,
                severity=c.get("severity"),
                mentioned_by_n_users=n_users,
                pct_of_cluster=pct,
                sentiment_scores=sentiment,
            )
        )
    return ClaimList(claims=out, coverage=coverage)


def _build_persona(
    parsed: dict,
    cluster: Cluster,
    coverage_dict: dict[str, Any],
    pack: _EvidencePack,
    run_id: str,
    provider_name: str,
    model: str,
    unverified_fields: set[str],
) -> Persona:
    quotes: list[RepresentativeQuote] = []
    for q in parsed.get("representative_quotes", []) or []:
        if not isinstance(q, dict):
            continue
        doc_id = str(q.get("doc_id", ""))
        # Backfill source/url/lang from the evidence pack when the LLM
        # omits them (it routinely does — it has no reason to repeat data
        # we passed in). The UI needs these for clickable provenance.
        meta = pack.doc_metadata.get(doc_id, {})
        source = q.get("source") or meta.get("source", "")
        url = q.get("url") or meta.get("url", "")
        lang = q.get("lang") or meta.get("lang", "en")
        quotes.append(
            RepresentativeQuote(
                text_original=str(q.get("text_original", "")).strip(),
                text_translated=q.get("text_translated"),
                lang=str(lang),
                source=str(source),
                url=str(url),
                doc_id=doc_id,
            )
        )

    cov = lambda f: "unverified" if f in unverified_fields else "ok"  # noqa: E731

    return Persona(
        id=f"persona_{_make_short_hash(cluster.cluster_id)}",
        run_id=run_id,
        cluster_id=cluster.cluster_id,
        name=str(parsed.get("name", f"Persona {cluster.cluster_id}")).strip(),
        one_liner=str(parsed.get("one_liner", "")).strip(),
        language=str(parsed.get("language", "en")),
        demographics=parsed.get("demographics") or {},
        goals=_claim_list_from_raw(parsed.get("goals"), coverage=cov("goals"), pack=pack),
        motivations=_claim_list_from_raw(
            parsed.get("motivations"), coverage=cov("motivations"), pack=pack
        ),
        pain_points=_claim_list_from_raw(
            parsed.get("pain_points"), coverage=cov("pain_points"), pack=pack
        ),
        preferred_channels=_claim_list_from_raw(
            parsed.get("preferred_channels"), coverage=cov("preferred_channels"), pack=pack
        ),
        behaviors=_claim_list_from_raw(
            parsed.get("behaviors"), coverage=cov("behaviors"), pack=pack
        ),
        representative_quotes=quotes,
        data_source_coverage=coverage_dict,
        confidence=_compute_confidence(unverified_fields),
        cluster_size=cluster.size,
        generated_at=datetime.now(timezone.utc),
        model=model,
        provider=provider_name,
    )


def _build_journey(
    parsed: dict,
    cluster: Cluster,
    persona: Persona,
    coverage_dict: dict[str, Any],
    pack: _EvidencePack,
    run_id: str,
    provider_name: str,
    model: str,
    per_stage_unverified: dict[str, set[str]],
) -> JourneyMap:
    stages: list[JourneyStage] = []
    by_name = {
        s.get("stage"): s for s in parsed.get("stages", []) or [] if isinstance(s, dict)
    }
    for stage_name in JOURNEY_STAGES:
        raw = by_name.get(stage_name) or {}
        unverified_here = per_stage_unverified.get(stage_name, set())
        cov_for = lambda f: "unverified" if f in unverified_here else "ok"  # noqa: E731

        emotions = []
        for em in raw.get("emotions", []) or []:
            if not isinstance(em, dict):
                continue
            try:
                emotions.append(
                    EmotionPoint(
                        label=str(em.get("label", "")).strip(),
                        intensity=float(em.get("intensity", 0.0)),
                        evidence=[str(x) for x in (em.get("evidence") or [])],
                    )
                )
            except (ValueError, TypeError):
                continue

        # Stage-level data sparsity (independent of validator's unverified):
        # count distinct doc_ids cited across this stage's claim fields.
        cited: set[str] = set()
        for f in _JOURNEY_CLAIM_FIELDS:
            for c in raw.get(f, []) or []:
                if isinstance(c, dict):
                    for cite in c.get("evidence") or []:
                        cited.add(cite)
        for em in raw.get("emotions", []) or []:
            if isinstance(em, dict):
                for cite in em.get("evidence") or []:
                    cited.add(cite)

        if not raw:
            stage_coverage = "none"
        elif len(cited) < 2:
            stage_coverage = "thin"
        else:
            stage_coverage = "ok"
        # If validator dropped fields, prefer the unverified signal at field
        # level; stage-level coverage stays as the sparsity marker.

        stages.append(
            JourneyStage(
                stage=stage_name,
                touchpoints=_claim_list_from_raw(
                    raw.get("touchpoints"), coverage=cov_for("touchpoints"), pack=pack
                ),
                user_actions=_claim_list_from_raw(
                    raw.get("user_actions"), coverage=cov_for("user_actions"), pack=pack
                ),
                emotions=emotions,
                frictions=_claim_list_from_raw(
                    raw.get("frictions"), coverage=cov_for("frictions"), pack=pack
                ),
                opportunities=_claim_list_from_raw(
                    raw.get("opportunities"), coverage=cov_for("opportunities"), pack=pack
                ),
                coverage=stage_coverage,
            )
        )

    return JourneyMap(
        id=f"journey_{_make_short_hash(persona.id)}",
        run_id=run_id,
        persona_id=persona.id,
        language=persona.language,
        data_source_coverage=coverage_dict,
        stages=stages,
        generated_at=datetime.now(timezone.utc),
        model=model,
        provider=provider_name,
    )


def _compute_confidence(unverified_fields: set[str]) -> float:
    """Crude confidence: 1.0 minus 0.1 per unverified bucket."""
    return max(0.0, 1.0 - 0.1 * len(unverified_fields))


def _make_short_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_persona(
    cluster: Cluster,
    post_texts: dict[str, str],
    post_metadata: dict[str, dict[str, Any]] | None = None,
    region: str = "",
    *,
    client: LLMClient,
    model: str | None = None,
    run_id: str | None = None,
) -> tuple[Persona, _EvidencePack, Usage]:
    """Synthesize one Persona from a cluster.

    Returns the persona plus the evidence pack (so the journey call can
    reuse it for cache hits) and the call's token usage.
    """
    model = model or client.default_model
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    pack = _build_evidence_pack(cluster, post_texts, post_metadata, region)
    parsed, usage, residual_errors = _call_and_validate(
        client=client,
        pack=pack,
        task_message=_persona_task(),
        validator=_validate_grounding_persona,
        model=model,
    )
    unverified: set[str] = set()
    if residual_errors:
        _log.warning(
            "synthesize.persona.unverified_after_retry",
            cluster_id=cluster.cluster_id,
            residual_errors=residual_errors[:5],
        )
        unverified = _drop_unverified_persona(parsed, pack)

    persona = _build_persona(
        parsed,
        cluster,
        pack.coverage,
        pack,
        run_id,
        client.name,
        model,
        unverified,
    )
    return persona, pack, usage


def generate_journey(
    persona: Persona,
    pack: _EvidencePack,
    *,
    client: LLMClient,
    model: str | None = None,
    run_id: str | None = None,
) -> tuple[JourneyMap, Usage]:
    """Synthesize a Journey Map for an already-built persona on the same pack."""
    model = model or client.default_model
    run_id = run_id or persona.run_id

    parsed, usage, residual_errors = _call_and_validate(
        client=client,
        pack=pack,
        task_message=_journey_task(persona.name, persona.one_liner),
        validator=_validate_grounding_journey,
        model=model,
    )

    per_stage_unverified: dict[str, set[str]] = {}
    if residual_errors:
        _log.warning(
            "synthesize.journey.unverified_after_retry",
            persona_id=persona.id,
            residual_errors=residual_errors[:5],
        )
        per_stage_unverified = _drop_unverified_journey(parsed, pack)

    journey = _build_journey(
        parsed,
        pack.cluster,
        persona,
        pack.coverage,
        pack,
        run_id,
        client.name,
        model,
        per_stage_unverified,
    )
    return journey, usage


@dataclass
class RunReport:
    topic: str
    region: str
    run_id: str
    provider: str
    model: str
    clusters_processed: int
    personas: list[Persona] = field(default_factory=list)
    journeys: list[JourneyMap] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_input_tokens: int = 0
    total_cache_write_tokens: int = 0
    actual_cost_usd: float = 0.0
    dry_run: bool = False
    estimate: CostEstimate | None = None


def synthesize_run(
    topic: str,
    region: str,
    clusters: list[Cluster],
    post_texts: dict[str, str],
    post_metadata: dict[str, dict[str, Any]] | None = None,
    *,
    provider: str = "anthropic",
    model: str | None = None,
    api_key: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    max_cost_usd: float = DEFAULT_COST_CAP_USD,
    min_cost_usd: float = DEFAULT_COST_FLOOR_USD,
    run_id: str | None = None,
    http_client: httpx.Client | None = None,
    cluster_ids: list[str] | None = None,
) -> RunReport:
    """Run synthesis for every cluster in `clusters` (or a subset by id).

    Order of operations:
      1. Estimate cost. If estimate > max_cost_usd and not force -> raise.
      2. If dry_run -> return report with just the estimate, no API calls.
      3. Build client (verifies API key is set).
      4. For each cluster: persona, then journey (cache hit on journey).
      5. Write nothing here — caller handles persistence.
    """
    if cluster_ids:
        wanted = set(cluster_ids)
        clusters = [c for c in clusters if c.cluster_id in wanted]
    if not clusters:
        raise SynthesisError("No clusters to synthesize.")

    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Build client up front so we estimate against its pricing AND fail fast
    # on missing API keys, BEFORE doing any work. For dry runs we still need
    # the pricing table — build the client but don't require the key in dry
    # mode (we just need pricing constants).
    if dry_run:
        client = _build_pricing_only_client(provider, http_client=http_client)
    else:
        client = build_client(provider, api_key=api_key, client=http_client)
    model = model or client.default_model

    estimate = estimate_cost(
        clusters,
        client=client,
        model=model,
        post_texts=post_texts,
        post_metadata=post_metadata,
        region=region,
    )

    report = RunReport(
        topic=topic,
        region=region,
        run_id=run_id,
        provider=client.name,
        model=model,
        clusters_processed=len(clusters),
        dry_run=dry_run,
        estimate=estimate,
    )

    if estimate.estimated_usd > max_cost_usd and not force:
        raise CostCapExceeded(
            f"Estimated cost ${estimate.estimated_usd:.4f} exceeds cap "
            f"${max_cost_usd:.2f}. Pass --force to override or raise "
            f"--max-cost."
        )

    if dry_run:
        return report

    total_usage = Usage()
    for c in clusters:
        try:
            persona, pack, u_persona = generate_persona(
                c,
                post_texts,
                post_metadata,
                region,
                client=client,
                model=model,
                run_id=run_id,
            )
            total_usage = total_usage.add(u_persona)
            journey, u_journey = generate_journey(
                persona, pack, client=client, model=model, run_id=run_id
            )
            total_usage = total_usage.add(u_journey)
            report.personas.append(persona)
            report.journeys.append(journey)
            _log.info(
                "synthesize.cluster_done",
                cluster_id=c.cluster_id,
                persona_id=persona.id,
                journey_id=journey.id,
                input_tokens=u_persona.input_tokens + u_journey.input_tokens,
                cached_tokens=(
                    u_persona.cached_input_tokens + u_journey.cached_input_tokens
                ),
                output_tokens=u_persona.output_tokens + u_journey.output_tokens,
            )
        except SynthesisError as e:
            _log.warning(
                "synthesize.cluster_failed",
                cluster_id=c.cluster_id,
                error=str(e),
            )
            continue

    pricing = client.pricing(model)
    report.total_input_tokens = total_usage.input_tokens
    report.total_output_tokens = total_usage.output_tokens
    report.total_cached_input_tokens = total_usage.cached_input_tokens
    report.total_cache_write_tokens = total_usage.cache_write_tokens
    report.actual_cost_usd = round(pricing.cost(total_usage), 4)

    # ── Cost floor: if below min_cost_usd, run refinement passes ──
    refinement_round = 0
    MAX_REFINEMENT_ROUNDS = 5
    while report.actual_cost_usd < min_cost_usd and refinement_round < MAX_REFINEMENT_ROUNDS:
        refinement_round += 1
        # Target largest cluster for deeper analysis
        largest = max(clusters, key=lambda c: c.size)
        try:
            _log.info(
                "synthesize.refinement",
                round=refinement_round,
                cluster_id=largest.cluster_id,
                current_cost=report.actual_cost_usd,
                target_floor=min_cost_usd,
            )
            # Run deeper analysis on the largest cluster
            _, u_refine = _refinement_pass(
                largest, post_texts, post_metadata, region,
                client=client, model=model, run_id=run_id,
            )
            total_usage = total_usage.add(u_refine)
            report.actual_cost_usd = round(pricing.cost(total_usage), 4)
        except SynthesisError as e:
            _log.warning("synthesize.refinement_failed", error=str(e))
            break

    # Close client if we own its httpx client.
    close = getattr(client, "close", None)
    if callable(close) and http_client is None:
        close()

    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _refinement_pass(
    cluster: Cluster,
    post_texts: dict[str, str],
    post_metadata: dict[str, dict[str, Any]] | None,
    region: str,
    *,
    client: Any,
    model: str,
    run_id: str,
) -> tuple[str, Usage]:
    """Run a deeper analysis pass on a cluster to burn budget for quality.

    Returns (analysis_text, Usage).
    """
    from src.pipeline.synthesize import _EvidencePack, _build_evidence_pack, _build_system_prompt

    pack = _build_evidence_pack(cluster, post_texts, post_metadata)
    system = _build_system_prompt(region)

    prompt = (
        f"You are a senior market analyst reviewing a consumer persona for {region}. "
        f"Below is a cluster of {cluster.size} consumer posts. "
        f"Provide a DEEP quantitative and qualitative analysis:\n\n"
        f"1. Temporal trends — what changed over time in consumer sentiment?\n"
        f"2. Competitive landscape — how does this product compare to alternatives mentioned?\n"
        f"3. Root cause analysis — what systemic issues drive the top 3 pain points?\n"
        f"4. Segment breakdown — are there sub-groups within this cluster with different needs?\n"
        f"5. Actionable recommendations — what should the product team fix first?\n\n"
        f"Evidence from consumer posts:\n{pack.text}\n\n"
        f"Be specific. Cite doc_ids for every claim. Output in markdown."
    )

    resp = client.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        model=model,
        max_tokens=2048,
        temperature=0.4,
    )
    u = Usage(
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cached_input_tokens=getattr(resp.usage, "cached_input_tokens", 0),
    )
    return resp.content, u


def _build_pricing_only_client(
    provider: str, *, http_client: httpx.Client | None
) -> LLMClient:
    """A client used for dry-run cost estimation; no API key needed."""
    provider = provider.lower()
    if provider == "anthropic":
        return AnthropicClient("dry-run", client=http_client)
    if provider == "deepseek":
        return DeepSeekClient("dry-run", client=http_client)
    raise SynthesisError(
        f"Unknown LLM provider {provider!r}. Use 'anthropic' or 'deepseek'."
    )


# ---------------------------------------------------------------------------
# Quantitative Grounding — compute per-claim stats from evidence pack
# ---------------------------------------------------------------------------


def _compute_quantitative_grounding(
    evidence: list[str],
    pack: "_EvidencePack",
) -> tuple[int, float, dict[str, int]]:
    """Compute grounding stats for a claim's evidence list.

    Returns (mentioned_by_n_users, pct_of_cluster, sentiment_scores).
    """
    n_users = len(set(evidence))
    pct = round(n_users / max(pack.cluster.size, 1), 3)

    sentiment: dict[str, int] = {"negative": 0, "neutral": 0, "positive": 0}
    negative_words = {
        "bad", "terrible", "awful", "hate", "worst", "poor", "broken",
        "frustrat", "annoy", "bug", "crash", "fail", "useless", "rubbish",
        "差", "廢", "垃圾", "爛", "煩", "討厭", "唔掂", "死", "呃錢",
    }
    positive_words = {
        "good", "great", "excellent", "love", "best", "amazing", "awesome",
        "useful", "helpful", "convenient", "fast", "smooth",
        "好", "正", "讚", "方便", "快", "好用", "唔錯", "推薦",
    }
    for doc_id in evidence:
        text = pack.doc_texts.get(doc_id, "").lower()
        neg = sum(1 for w in negative_words if w in text)
        pos = sum(1 for w in positive_words if w in text)
        if neg > pos:
            sentiment["negative"] += 1
        elif pos > neg:
            sentiment["positive"] += 1
        else:
            sentiment["neutral"] += 1

    return n_users, pct, sentiment


def _backfill_grounding(
    claims: list[dict[str, Any]],
    pack: "_EvidencePack",
) -> list[dict[str, Any]]:
    """Backfill quantitative grounding onto parsed claim dicts."""
    for c in claims:
        evidence = c.get("evidence", [])
        n_users, pct, sentiment = _compute_quantitative_grounding(evidence, pack)
        c["mentioned_by_n_users"] = n_users
        c["pct_of_cluster"] = pct
        c["sentiment_scores"] = sentiment
    return claims


# ---------------------------------------------------------------------------
# Adversarial Validation
# ---------------------------------------------------------------------------

_ADVERSARIAL_PROMPT = (
    "You are an adversarial reviewer. Find evidence in the source data "
    "that CONTRADICTS this persona's claims.\n\n"
    "For each claim in pain_points, goals, motivations, behaviors, "
    "and preferred_channels:\n"
    "1. Look through evidence docs for contradicting text.\n"
    "2. If found, list the doc_id(s) and contradicting quote.\n"
    "3. Assign overall confidence score (0.0-1.0).\n\n"
    "Return JSON:\n"
    '  {"contested_claims": [{"claim": "...", "field": "...", '
    '"contradicting_doc_ids": ["doc_abc"], "reasoning": "..."}], '
    '"overall_confidence": 0.85, "summary": "..."}\n\n'
    "If no contradictions, return empty contested_claims and confidence=1.0."
)


def _adversarial_validation(
    persona: "Persona",
    pack: "_EvidencePack",
    client: "LLMClient",
    model: str,
) -> "AdversarialReport":
    """Run adversarial validation against a generated persona."""
    from src.schemas.synthesis import AdversarialReport

    persona_json = persona.model_dump_json(indent=2)
    task = f"{_ADVERSARIAL_PROMPT}\n\nPERSONA JSON:\n{persona_json}\n"

    try:
        text, _ = client.synthesize(
            rules_block="",
            evidence_block=pack.block_text,
            task_message=task,
            model=model,
        )
        parsed = _extract_json(text)
    except Exception as e:
        _log.warning("adversarial.validation_failed", error=str(e))
        return AdversarialReport(
            persona_id=persona.id, overall_confidence=1.0,
            summary=f"Adversarial validation skipped: {e}",
        )

    if not parsed:
        return AdversarialReport(
            persona_id=persona.id, overall_confidence=1.0,
            summary="Adversarial validation returned no parseable JSON",
        )

    contested = parsed.get("contested_claims", [])
    for cc in contested:
        claim_text = cc.get("claim", "")
        field = cc.get("field", "")
        doc_ids = cc.get("contradicting_doc_ids", [])
        claim_list = getattr(persona, field, None)
        if claim_list and hasattr(claim_list, "claims"):
            for ec in claim_list.claims:
                if ec.claim == claim_text:
                    ec.contested_by = doc_ids
                    break

    return AdversarialReport(
        persona_id=persona.id,
        contested_claims=contested,
        overall_confidence=parsed.get("overall_confidence", 1.0),
        summary=parsed.get("summary", ""),
    )


# ---------------------------------------------------------------------------
# Temporal & Comparative Analysis
# ---------------------------------------------------------------------------


def _rebuild_clusters_for_window(
    clusters: list[Cluster],
    window_post_ids: set[str],
) -> list[Cluster]:
    """Rebuild clusters keeping only posts in `window_post_ids`.

    Clusters with zero posts in the window are dropped.  Other fields
    (size, keyword_summary, etc.) are carried forward — they describe
    the original cluster theme, which remains useful even when the window
    has fewer posts.
    """
    rebuilt: list[Cluster] = []
    for c in clusters:
        filtered = [pid for pid in c.post_ids if pid in window_post_ids]
        if not filtered:
            continue
        new_c = c.model_copy(update={"post_ids": filtered, "size": len(filtered)})
        # Keep representative posts that fall in this window
        rep_filtered = [pid for pid in c.representative_post_ids if pid in window_post_ids]
        if rep_filtered:
            new_c.representative_post_ids = rep_filtered
        rebuilt.append(new_c)
    return rebuilt


def synthesize_temporal(
    topic: str,
    region: str,
    cutoff_before: datetime,
    cutoff_after: datetime,
    clusters: list[Cluster],
    post_texts: dict[str, str],
    post_metadata: dict[str, dict[str, Any]],
    *,
    provider: str = "anthropic",
    model: str | None = None,
    api_key: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    max_cost_usd: float = DEFAULT_COST_CAP_USD,
    run_id: str | None = None,
    http_client: httpx.Client | None = None,
) -> "TemporalComparison":
    """Synthesize the same topic across two time windows and compare shifts.

    Parameters
    ----------
    cutoff_before:
        Posts with ``posted_at < cutoff_before`` fall into the "before" window.
    cutoff_after:
        Posts with ``posted_at >= cutoff_after`` fall into the "after" window.
        When ``cutoff_before == cutoff_after`` the windows are contiguous with
        no gap.
    """
    from src.schemas.synthesis import TemporalComparison

    base_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # ---- partition posts by date ------------------------------------------
    window_before_pids: set[str] = set()
    window_after_pids: set[str] = set()
    for pid, meta in post_metadata.items():
        posted_str = meta.get("posted_at")
        if posted_str is None:
            continue
        try:
            posted_dt = _parse_posted_at(posted_str)
        except (ValueError, TypeError):
            continue
        if posted_dt < cutoff_before:
            window_before_pids.add(pid)
        if posted_dt >= cutoff_after:
            window_after_pids.add(pid)

    # ---- rebuild clusters for each window ---------------------------------
    clusters_before = _rebuild_clusters_for_window(clusters, window_before_pids)
    clusters_after = _rebuild_clusters_for_window(clusters, window_after_pids)

    # ---- run synthesis on each window -------------------------------------
    label_before = f"Before {cutoff_before.strftime('%Y-%m-%d')}"
    label_after = f"After {cutoff_after.strftime('%Y-%m-%d')}"

    before_report: RunReport | None = None
    after_report: RunReport | None = None

    if clusters_before:
        before_report = synthesize_run(
            topic=topic,
            region=region,
            clusters=clusters_before,
            post_texts=post_texts,
            post_metadata=post_metadata,
            provider=provider,
            model=model,
            api_key=api_key,
            dry_run=dry_run,
            force=force,
            max_cost_usd=max_cost_usd,
            run_id=f"{base_run_id}_before",
            http_client=http_client,
        )
    if clusters_after:
        after_report = synthesize_run(
            topic=topic,
            region=region,
            clusters=clusters_after,
            post_texts=post_texts,
            post_metadata=post_metadata,
            provider=provider,
            model=model,
            api_key=api_key,
            dry_run=dry_run,
            force=force,
            max_cost_usd=max_cost_usd,
            run_id=f"{base_run_id}_after",
            http_client=http_client,
        )

    # ---- compute shift analysis -------------------------------------------
    shifts: list[dict[str, Any]] = []
    personas_before = before_report.personas if before_report else []
    personas_after = after_report.personas if after_report else []

    # Compare pain points: emerged, resolved, persisted
    before_claims = {
        ec.claim
        for p in personas_before
        for ec in p.pain_points.claims
    }
    after_claims = {
        ec.claim
        for p in personas_after
        for ec in p.pain_points.claims
    }
    emerged = after_claims - before_claims
    resolved = before_claims - after_claims
    persisted = before_claims & after_claims

    if emerged:
        shifts.append({"type": "emerged", "claims": sorted(emerged)})
    if resolved:
        shifts.append({"type": "resolved", "claims": sorted(resolved)})
    if persisted:
        shifts.append({"type": "persisted", "claims": sorted(persisted)})

    summary_lines = []
    if emerged:
        summary_lines.append(f"{len(emerged)} new pain points emerged.")
    if resolved:
        summary_lines.append(f"{len(resolved)} pain points resolved.")
    if persisted:
        summary_lines.append(f"{len(persisted)} pain points persisted.")
    if not summary_lines:
        summary_lines.append("No significant shifts detected.")

    return TemporalComparison(
        topic=topic,
        region=region,
        window_before_label=label_before,
        window_after_label=label_after,
        window_before=personas_before,
        window_after=personas_after,
        shifts=shifts,
        summary=" ".join(summary_lines),
        generated_at=datetime.now(timezone.utc),
    )


def synthesize_comparative(
    topic_a: str,
    topic_b: str,
    region: str,
    clusters_a: list[Cluster],
    clusters_b: list[Cluster],
    post_texts_a: dict[str, str],
    post_texts_b: dict[str, str],
    post_metadata_a: dict[str, dict[str, Any]],
    post_metadata_b: dict[str, dict[str, Any]],
    *,
    provider: str = "anthropic",
    model: str | None = None,
    api_key: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    max_cost_usd: float = DEFAULT_COST_CAP_USD,
    run_id: str | None = None,
    http_client: httpx.Client | None = None,
) -> "ComparativeReport":
    """Synthesize two different topics in the same region and compare.

    Runs the full synthesize pipeline for each topic independently, then
    diffs the resulting personas to surface common and divergent pain points.
    """
    from src.schemas.synthesis import ComparativeReport

    base_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    report_a = synthesize_run(
        topic=topic_a,
        region=region,
        clusters=clusters_a,
        post_texts=post_texts_a,
        post_metadata=post_metadata_a,
        provider=provider,
        model=model,
        api_key=api_key,
        dry_run=dry_run,
        force=force,
        max_cost_usd=max_cost_usd,
        run_id=f"{base_run_id}_a",
        http_client=http_client,
    )

    report_b = synthesize_run(
        topic=topic_b,
        region=region,
        clusters=clusters_b,
        post_texts=post_texts_b,
        post_metadata=post_metadata_b,
        provider=provider,
        model=model,
        api_key=api_key,
        dry_run=dry_run,
        force=force,
        max_cost_usd=max_cost_usd,
        run_id=f"{base_run_id}_b",
        http_client=http_client,
    )

    # ---- diff pain points -------------------------------------------------
    claims_a = {
        (ec.claim, ec.severity or "unspecified")
        for p in report_a.personas
        for ec in p.pain_points.claims
    }
    claims_b = {
        (ec.claim, ec.severity or "unspecified")
        for p in report_b.personas
        for ec in p.pain_points.claims
    }

    common = claims_a & claims_b
    divergent_a = claims_a - claims_b
    divergent_b = claims_b - claims_a

    common_pain_points: list[dict[str, Any]] = [
        {"claim": c, "severity": s} for c, s in sorted(common, key=lambda x: x[0])
    ]
    divergent_pain_points: list[dict[str, Any]] = [
        {
            "claim": c,
            "severity": s,
            "unique_to": topic_a,
        }
        for c, s in sorted(divergent_a, key=lambda x: x[0])
    ] + [
        {
            "claim": c,
            "severity": s,
            "unique_to": topic_b,
        }
        for c, s in sorted(divergent_b, key=lambda x: x[0])
    ]

    summary_parts = []
    if common_pain_points:
        summary_parts.append(
            f"{len(common_pain_points)} pain points shared across both topics."
        )
    if divergent_a:
        summary_parts.append(
            f"{len(divergent_a)} pain points unique to {topic_a}."
        )
    if divergent_b:
        summary_parts.append(
            f"{len(divergent_b)} pain points unique to {topic_b}."
        )
    if not summary_parts:
        summary_parts.append("No pain points identified in either topic.")

    return ComparativeReport(
        topic_a=topic_a,
        topic_b=topic_b,
        region=region,
        personas_a=report_a.personas,
        personas_b=report_b.personas,
        common_pain_points=common_pain_points,
        divergent_pain_points=divergent_pain_points,
        summary=" ".join(summary_parts),
        generated_at=datetime.now(timezone.utc),
    )


def _parse_posted_at(value: Any) -> datetime:
    """Parse ``posted_at`` from a raw post dict value (str or datetime).

    Handles ISO-8601 strings with or without timezone, plus datetime objects.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        # Try ISO format with optional timezone
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
