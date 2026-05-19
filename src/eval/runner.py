"""Eval runner — scores synthesized personas against ground-truth fixtures.

Each fixture (JSON, under ``eval/products/``) freezes a small synthetic
dataset:

  - ``topic`` / ``region``  — what the pipeline thinks it's analyzing
  - ``posts``               — 10-15 fake posts with id / body / source / url / lang
  - ``clusters``            — 2-3 pre-clustered groupings
  - ``expected_pain_points``— hand-curated themes the LLM should surface
  - ``mock_persona_responses`` / ``mock_journey_responses``
                            — canned LLM replies keyed by cluster_id
                              (only used when ``provider="mock"``)

Two scores:

  - ``recovery_rate``       — fraction of expected pain points recovered.
                              A theme counts as recovered when any
                              persona's pain-point claim text overlaps
                              the theme's keyword bag (case-insensitive,
                              ≥1 keyword match).
  - ``mean_coverage_score`` — average ``coverage_tier`` mapped 1..4
                              ("single-perspective" → 1, "high" → 4).
                              Independent of recovery; tracks whether
                              the persona was built from enough source
                              categories.

The runner is provider-agnostic: pass ``provider="mock"`` to use the
fixture's canned LLM responses (CI / regression tests), or
``provider="anthropic" | "deepseek"`` to drive the real API.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.pipeline.synthesize import (
    AnthropicClient,
    RunReport,
    _doc_id_for,
    synthesize_run,
)
from src.schemas.cluster import Cluster

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

EVAL_DIR = Path(__file__).resolve().parent.parent.parent / "eval" / "products"

_COVERAGE_SCORES = {
    "single-perspective": 1,
    "limited": 2,
    "balanced": 3,
    "high": 4,
}


@dataclass
class EvalScore:
    """Result of scoring one fixture."""

    name: str
    topic: str
    region: str
    expected_pain_points: int
    recovered_pain_points: int
    recovery_rate: float
    mean_coverage_score: float
    personas_generated: int
    unmatched_themes: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    """Aggregated eval-suite results."""

    scores: list[EvalScore]
    mean_recovery_rate: float
    mean_coverage_score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "scores": [s.__dict__ for s in self.scores],
            "mean_recovery_rate": self.mean_recovery_rate,
            "mean_coverage_score": self.mean_coverage_score,
        }


def load_fixture(path: Path | str) -> dict[str, Any]:
    """Load a JSON eval fixture from disk."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def list_fixtures(directory: Path | str | None = None) -> list[Path]:
    """Enumerate eval fixtures in alphabetical order."""
    root = Path(directory) if directory else EVAL_DIR
    if not root.exists():
        return []
    return sorted(root.glob("*.json"))


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


class _MockTransport:
    """httpx mock transport — replays canned persona/journey responses.

    Each call pops the next scripted reply off the queue. The runner
    interleaves persona-then-journey for every cluster, so the queue
    must be built in that order.
    """

    def __init__(self, replies: list[dict[str, Any]]):
        self._queue = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._queue:
            raise AssertionError(
                "mock transport exhausted — fixture is missing a "
                "persona or journey response"
            )
        return httpx.Response(200, json=self._queue.pop(0))


def _claude_envelope(content: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": content}],
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }


_PLACEHOLDER_RE = re.compile(r"<(post_[A-Za-z0-9_-]+)>")


def _resolve_placeholders(node: Any) -> Any:
    """Replace ``<post_XXX>`` tokens with the real ``doc_<sha12>`` ids.

    Fixture authors don't want to compute sha256 by hand, so the mock
    responses use ``<post_001>`` placeholders. The runner walks the
    structure and rewrites them — strings, list items, dict values, all
    levels — using the same hash the synthesizer uses.
    """
    if isinstance(node, str):
        return _PLACEHOLDER_RE.sub(lambda m: _doc_id_for(m.group(1)), node)
    if isinstance(node, list):
        return [_resolve_placeholders(x) for x in node]
    if isinstance(node, dict):
        return {k: _resolve_placeholders(v) for k, v in node.items()}
    return node


def _build_mock_client(
    fixture: dict[str, Any],
    cluster_ids: list[str],
) -> tuple[AnthropicClient, _MockTransport]:
    """Stitch a mock client whose responses come from ``fixture``."""
    personas = fixture.get("mock_persona_responses", {})
    journeys = fixture.get("mock_journey_responses", {})
    replies: list[dict[str, Any]] = []
    for cid in cluster_ids:
        if cid not in personas or cid not in journeys:
            raise KeyError(
                f"fixture {fixture.get('name')!r} missing mock response "
                f"for cluster {cid!r}"
            )
        persona = _resolve_placeholders(personas[cid])
        journey = _resolve_placeholders(journeys[cid])
        replies.append(_claude_envelope(json.dumps(persona)))
        replies.append(_claude_envelope(json.dumps(journey)))
    transport = _MockTransport(replies)
    http_client = httpx.Client(transport=httpx.MockTransport(transport))
    return AnthropicClient("mock-key", client=http_client), transport


# ---------------------------------------------------------------------------
# Fixture → synthesize_run inputs
# ---------------------------------------------------------------------------


def _build_inputs(
    fixture: dict[str, Any],
) -> tuple[list[Cluster], dict[str, str], dict[str, dict[str, Any]]]:
    """Convert fixture posts + clusters into the shapes synthesize_run wants."""
    posts = fixture["posts"]
    post_texts = {p["id"]: p["body"] for p in posts}
    post_metadata = {
        p["id"]: {
            "source": p["source"],
            "url": p["url"],
            "lang": p.get("lang", "en"),
        }
        for p in posts
    }
    clusters: list[Cluster] = []
    for c in fixture["clusters"]:
        clusters.append(
            Cluster(
                cluster_id=c["cluster_id"],
                topic=fixture["topic"],
                region=fixture["region"],
                size=len(c["post_ids"]),
                post_ids=c["post_ids"],
                representative_post_ids=c["post_ids"][:3],
                keyword_summary=c.get("keyword_summary", []),
                source_distribution=c.get("source_distribution", {}),
                language_distribution=c.get("language_distribution", {}),
                generated_at=datetime.now(timezone.utc),
            )
        )
    return clusters, post_texts, post_metadata


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _theme_recovered(
    theme: dict[str, Any],
    persona_pain_points: list[str],
) -> bool:
    """A theme is recovered when any pain-point claim shares ≥1 keyword.

    Keywords are matched case-insensitively against the alphanumeric
    tokens of the claim — so "Battery drain" recovers a claim like
    "App drains the battery overnight". When ``match_any`` is given it
    overrides per-keyword matching with substring search instead, which
    is useful for multi-word phrases where token overlap is too loose.
    """
    keyword_bag = {k.lower() for k in theme.get("keywords", [])}
    phrase_bag = [p.lower() for p in theme.get("phrases", [])]
    for claim in persona_pain_points:
        claim_lower = claim.lower()
        if any(phrase in claim_lower for phrase in phrase_bag):
            return True
        claim_tokens = _tokenize(claim)
        if keyword_bag & claim_tokens:
            return True
    return False


def _score_report(
    fixture: dict[str, Any], report: RunReport,
) -> EvalScore:
    expected = fixture.get("expected_pain_points", [])
    pain_claims: list[str] = []
    for p in report.personas:
        pain_claims.extend(c.claim for c in p.pain_points.claims)
    recovered = 0
    unmatched: list[str] = []
    for theme in expected:
        if _theme_recovered(theme, pain_claims):
            recovered += 1
        else:
            unmatched.append(theme.get("theme", ""))

    tier_scores = [
        _COVERAGE_SCORES.get(
            p.data_source_coverage.get("coverage_tier", ""), 0,
        )
        for p in report.personas
    ]
    mean_cov = sum(tier_scores) / len(tier_scores) if tier_scores else 0.0

    return EvalScore(
        name=fixture["name"],
        topic=fixture["topic"],
        region=fixture["region"],
        expected_pain_points=len(expected),
        recovered_pain_points=recovered,
        recovery_rate=(recovered / len(expected)) if expected else 0.0,
        mean_coverage_score=mean_cov,
        personas_generated=len(report.personas),
        unmatched_themes=unmatched,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_eval(
    fixture: dict[str, Any],
    *,
    provider: str = "mock",
    model: str | None = None,
    api_key: str | None = None,
) -> EvalScore:
    """Run synthesis against one fixture and score the result.

    ``provider="mock"`` plays back the fixture's canned LLM replies via
    a mocked httpx transport — useful in CI and during prompt iteration.
    Any other provider hits the real API via ``synthesize_run``.
    """
    clusters, post_texts, post_metadata = _build_inputs(fixture)
    cluster_ids = [c.cluster_id for c in clusters]

    if provider == "mock":
        client, _ = _build_mock_client(fixture, cluster_ids)
        report = synthesize_run(
            topic=fixture["topic"],
            region=fixture["region"],
            clusters=clusters,
            post_texts=post_texts,
            post_metadata=post_metadata,
            provider="anthropic",  # mock client is an AnthropicClient
            model=model,
            api_key="mock-key",
            http_client=client._client,  # type: ignore[attr-defined]
            min_cost_usd=0.0,
        )
    else:
        report = synthesize_run(
            topic=fixture["topic"],
            region=fixture["region"],
            clusters=clusters,
            post_texts=post_texts,
            post_metadata=post_metadata,
            provider=provider,
            model=model,
            api_key=api_key,
            min_cost_usd=0.0,
        )

    return _score_report(fixture, report)


def run_eval_suite(
    *,
    directory: Path | str | None = None,
    provider: str = "mock",
    model: str | None = None,
    api_key: str | None = None,
) -> EvalReport:
    """Run the full suite over every fixture under ``eval/products/``."""
    scores: list[EvalScore] = []
    for path in list_fixtures(directory):
        fixture = load_fixture(path)
        scores.append(
            run_eval(fixture, provider=provider, model=model, api_key=api_key)
        )
    if scores:
        mean_recovery = sum(s.recovery_rate for s in scores) / len(scores)
        mean_coverage = sum(s.mean_coverage_score for s in scores) / len(scores)
    else:
        mean_recovery = 0.0
        mean_coverage = 0.0
    return EvalReport(
        scores=scores,
        mean_recovery_rate=mean_recovery,
        mean_coverage_score=mean_coverage,
    )
