"""Tests for Phase 4 synthesis (Anthropic + DeepSeek backends, validator,
retry, coverage marking, cost cap, dry-run, API-key error).

All Claude/DeepSeek calls go through httpx.MockTransport so no network is
hit. Two response-script fixtures let tests inject a sequence of fake LLM
replies for the persona/journey/retry path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from src.pipeline.synthesize import (
    CostCapExceeded,
    DeepSeekClient,
    MissingAPIKey,
    SynthesisError,
    _build_coverage,
    _build_evidence_pack,
    _doc_id_for,
    _drop_unverified_journey,
    _drop_unverified_persona,
    _validate_grounding_journey,
    _validate_grounding_persona,
    estimate_cost,
    generate_journey,
    generate_persona,
    synthesize_run,
)
from src.schemas.cluster import Cluster


# ---------------------------------------------------------------------------
# Cluster + post fixtures
# ---------------------------------------------------------------------------


def _cluster(post_ids: list[str], cluster_id: str = "cluster_test") -> Cluster:
    return Cluster(
        cluster_id=cluster_id,
        topic="MTR Mobile",
        region="HK",
        size=len(post_ids),
        post_ids=post_ids,
        representative_post_ids=post_ids[:3],
        keyword_summary=["mtr", "mobile", "app", "payment", "octopus"],
        source_distribution={"lihkg": len(post_ids)},
        language_distribution={"zh": len(post_ids)},
        generated_at=datetime.now(timezone.utc),
    )


_POST_TEXTS = {
    "post_001": "用咗呢個 app 好多年, 介面好難用",
    "post_002": "Latest update made the app even slower.",
    "post_003": "Octopus reload is broken since last week.",
    "post_004": "Fare info shows wrong number sometimes.",
    "post_005": "App keeps crashing after iOS 18 update.",
}

_POST_META = {
    pid: {"source": "lihkg" if i % 2 else "reddit_old",
          "url": f"https://example.com/{pid}",
          "lang": "zh" if i % 2 else "en"}
    for i, pid in enumerate(_POST_TEXTS)
}


# ---------------------------------------------------------------------------
# Coverage / pack tests (no LLM)
# ---------------------------------------------------------------------------


def test_build_coverage_categories_from_registry() -> None:
    # Use source ids that ARE present in HK's regional registry: lihkg
    # (forums) + app_store_hk (reviews) → both categories present, the rest
    # missing.
    c = _cluster(["post_001", "post_002"])
    c = c.model_copy(update={"source_distribution": {"lihkg": 1, "app_store_hk": 1}})
    coverage = _build_coverage(c, region="HK")
    assert "forums" in coverage["categories_present"]
    assert "reviews" in coverage["categories_present"]
    assert "social" in coverage["categories_missing"]
    assert "video_comments" in coverage["categories_missing"]
    assert "qa" in coverage["categories_missing"]
    assert coverage["sources_used"] == ["lihkg", "app_store_hk"]
    assert coverage["doc_counts"] == {"lihkg": 1, "app_store_hk": 1}


def test_build_coverage_unknown_source_yields_no_category() -> None:
    """A scraper id not in the regional registry contributes to sources_used
    but to no category. This documents the existing reddit_old /
    reddit_hongkong_html registry inconsistency until it's fixed separately."""
    c = _cluster(["post_001"])
    c = c.model_copy(update={"source_distribution": {"reddit_old": 1}})
    coverage = _build_coverage(c, region="HK")
    assert coverage["categories_present"] == []
    assert coverage["sources_used"] == ["reddit_old"]


def test_evidence_pack_doc_ids_are_stable() -> None:
    c = _cluster(["post_001", "post_002", "post_003"])
    pack = _build_evidence_pack(c, _POST_TEXTS, _POST_META, region="HK")
    assert pack.doc_ids == {_doc_id_for("post_001"),
                            _doc_id_for("post_002"),
                            _doc_id_for("post_003")}
    # Re-build with same input -> same doc_ids.
    pack2 = _build_evidence_pack(c, _POST_TEXTS, _POST_META, region="HK")
    assert pack2.doc_ids == pack.doc_ids


# ---------------------------------------------------------------------------
# Grounding validator (no LLM)
# ---------------------------------------------------------------------------


def test_validator_flags_missing_evidence_array() -> None:
    c = _cluster(["post_001"])
    pack = _build_evidence_pack(c, _POST_TEXTS, _POST_META, region="HK")
    parsed = {"goals": [{"claim": "x", "evidence": []}]}
    errors = _validate_grounding_persona(parsed, pack)
    assert any("missing evidence" in e for e in errors)


def test_validator_flags_unknown_doc_id() -> None:
    c = _cluster(["post_001"])
    pack = _build_evidence_pack(c, _POST_TEXTS, _POST_META, region="HK")
    parsed = {"goals": [{"claim": "x", "evidence": ["doc_nonexistent00"]}]}
    errors = _validate_grounding_persona(parsed, pack)
    assert any("unknown doc_id" in e for e in errors)


def test_validator_flags_paraphrased_quote() -> None:
    c = _cluster(["post_001"])
    pack = _build_evidence_pack(c, _POST_TEXTS, _POST_META, region="HK")
    doc_id = _doc_id_for("post_001")
    parsed = {
        "goals": [{"claim": "g", "evidence": [doc_id]}],
        "representative_quotes": [
            {"text_original": "This is NOT in the post",
             "lang": "en", "doc_id": doc_id},
        ],
    }
    errors = _validate_grounding_persona(parsed, pack)
    assert any("not a verbatim substring" in e for e in errors)


def test_validator_accepts_verbatim_substring() -> None:
    c = _cluster(["post_001"])
    pack = _build_evidence_pack(c, _POST_TEXTS, _POST_META, region="HK")
    doc_id = _doc_id_for("post_001")
    parsed = {
        "goals": [{"claim": "g", "evidence": [doc_id]}],
        "representative_quotes": [
            {"text_original": "好難用",       # verbatim substring of post_001
             "lang": "zh", "doc_id": doc_id},
        ],
    }
    errors = _validate_grounding_persona(parsed, pack)
    assert errors == []


def test_journey_validator_walks_all_fields() -> None:
    c = _cluster(["post_001"])
    pack = _build_evidence_pack(c, _POST_TEXTS, _POST_META, region="HK")
    parsed = {
        "stages": [
            {
                "stage": "Awareness",
                "touchpoints": [{"claim": "x"}],            # missing evidence
                "user_actions": [],
                "emotions": [
                    {"label": "curious", "intensity": 0.5,
                     "evidence": ["doc_fakefake0000"]},     # unknown doc_id
                ],
                "frictions": [], "opportunities": [],
            }
        ]
    }
    errors = _validate_grounding_journey(parsed, pack)
    assert any("missing evidence" in e for e in errors)
    assert any("unknown doc_id" in e for e in errors)


def test_drop_unverified_keeps_good_drops_bad() -> None:
    c = _cluster(["post_001", "post_002"])
    pack = _build_evidence_pack(c, _POST_TEXTS, _POST_META, region="HK")
    good = _doc_id_for("post_001")
    parsed = {
        "goals": [
            {"claim": "good", "evidence": [good]},
            {"claim": "bad", "evidence": ["doc_unknown00000"]},
        ],
        "motivations": [{"claim": "y", "evidence": [good]}],
        "representative_quotes": [],
    }
    unverified = _drop_unverified_persona(parsed, pack)
    assert "goals" in unverified
    assert "motivations" not in unverified
    assert len(parsed["goals"]) == 1
    assert parsed["goals"][0]["claim"] == "good"


# ---------------------------------------------------------------------------
# Mocked-LLM end-to-end synthesis
# ---------------------------------------------------------------------------


class _ScriptedHTTP:
    """An httpx mock transport that replies with a queue of pre-baked bodies."""

    def __init__(self, responses: list[dict[str, Any]]):
        self._queue = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._queue:
            raise AssertionError(
                "scripted transport received an unexpected request"
            )
        body = self._queue.pop(0)
        return httpx.Response(200, json=body)


def _claude_reply(content: str, *, input_tokens: int = 1000,
                  output_tokens: int = 500,
                  cache_read_input_tokens: int = 0,
                  cache_creation_input_tokens: int = 0) -> dict[str, Any]:
    """Format a body that matches Anthropic's /v1/messages response shape."""
    return {
        "content": [{"type": "text", "text": content}],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
        },
    }


def _good_persona_json(doc_id: str) -> str:
    return json.dumps({
        "name": "Frustrated Daily Commuter",
        "one_liner": "Long-time HK MTR user who finds the mobile app slow.",
        "demographics": {"age_range": "25-45",
                         "occupation_examples": ["office worker"],
                         "evidence": [doc_id]},
        "goals": [{"claim": "Quick fare lookup", "evidence": [doc_id]}],
        "motivations": [{"claim": "Daily commute reliability",
                         "evidence": [doc_id]}],
        "pain_points": [{"claim": "Interface lag",
                         "severity": "high", "evidence": [doc_id]}],
        "preferred_channels": [{"claim": "LIHKG forum",
                                "evidence": [doc_id]}],
        "behaviors": [{"claim": "Posts frustration online",
                       "evidence": [doc_id]}],
        "representative_quotes": [
            {"text_original": "好難用", "lang": "zh", "doc_id": doc_id},
        ],
    })


def _bad_persona_then_good_persona(doc_id: str) -> list[str]:
    bad = json.dumps({
        "name": "X",
        "one_liner": "Y",
        "goals": [{"claim": "missing cite", "evidence": []}],  # missing evidence
        "motivations": [{"claim": "fake cite", "evidence": ["doc_NOTREAL000"]}],
        "pain_points": [],
        "preferred_channels": [],
        "behaviors": [],
        "representative_quotes": [],
    })
    return [bad, _good_persona_json(doc_id)]


def _good_journey_json(doc_id: str) -> str:
    stage_template = lambda name: {
        "stage": name,
        "touchpoints": [{"claim": f"{name} touchpoint", "evidence": [doc_id]}],
        "user_actions": [{"claim": f"{name} action", "evidence": [doc_id]}],
        "emotions": [{"label": "frustrated", "intensity": 0.7,
                      "evidence": [doc_id]}],
        "frictions": [{"claim": f"{name} friction", "evidence": [doc_id]}],
        "opportunities": [{"claim": f"{name} opp", "evidence": [doc_id]}],
    }
    return json.dumps({"stages": [
        stage_template(n) for n in
        ("Awareness", "Consideration", "Decision",
         "Onboarding", "Use", "Loyalty/Churn")
    ]})


def _make_anthropic_client(scripts: list[str], **claude_reply_kwargs: Any):
    from src.pipeline.synthesize import AnthropicClient

    responses = [_claude_reply(s, **claude_reply_kwargs) for s in scripts]
    transport = _ScriptedHTTP(responses)
    http_client = httpx.Client(transport=httpx.MockTransport(transport))
    return AnthropicClient("test-key", client=http_client), transport


def test_persona_generation_succeeds_on_first_try(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = _cluster(["post_001", "post_002"])
    doc_id = _doc_id_for("post_001")

    client, transport = _make_anthropic_client(
        [_good_persona_json(doc_id)],
    )
    persona, pack, usage = generate_persona(
        c, _POST_TEXTS, _POST_META, region="HK",
        client=client, run_id="r1",
    )
    assert persona.cluster_id == c.cluster_id
    assert persona.goals.coverage == "ok"
    assert persona.pain_points.claims[0].claim == "Interface lag"
    assert len(transport.requests) == 1  # no retry


def test_persona_retries_on_validation_failure_then_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = _cluster(["post_001", "post_002"])
    doc_id = _doc_id_for("post_001")

    client, transport = _make_anthropic_client(
        _bad_persona_then_good_persona(doc_id),
    )
    persona, _pack, _usage = generate_persona(
        c, _POST_TEXTS, _POST_META, region="HK",
        client=client, run_id="r1",
    )
    assert len(transport.requests) == 2  # one retry
    # Retry succeeded → all buckets coverage=ok.
    assert persona.goals.coverage == "ok"
    assert persona.motivations.coverage == "ok"


def test_persona_marks_unverified_when_retry_also_fails(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = _cluster(["post_001", "post_002"])

    # Both replies are bad: missing evidence on goals, unknown doc_id on motivations.
    bad = json.dumps({
        "name": "X", "one_liner": "Y",
        "goals": [{"claim": "g", "evidence": []}],
        "motivations": [{"claim": "m", "evidence": ["doc_NOPE00000000"]}],
        "pain_points": [], "preferred_channels": [], "behaviors": [],
        "representative_quotes": [],
    })
    client, transport = _make_anthropic_client([bad, bad])
    persona, _pack, _usage = generate_persona(
        c, _POST_TEXTS, _POST_META, region="HK",
        client=client, run_id="r1",
    )
    assert len(transport.requests) == 2
    assert persona.goals.coverage == "unverified"
    assert persona.motivations.coverage == "unverified"
    # Bad claims dropped from the kept lists.
    assert persona.goals.claims == []
    assert persona.motivations.claims == []
    # Other buckets stay ok.
    assert persona.pain_points.coverage == "ok"
    # Confidence took a hit.
    assert persona.confidence < 1.0


def test_journey_marks_thin_when_stage_has_one_doc_id(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = _cluster(["post_001", "post_002"])
    doc_id = _doc_id_for("post_001")

    # Build a journey where Awareness cites only ONE doc_id across all fields.
    thin_stage = lambda name: {
        "stage": name,
        "touchpoints": [{"claim": f"{name} t", "evidence": [doc_id]}],
        "user_actions": [],
        "emotions": [],
        "frictions": [],
        "opportunities": [],
    }
    rich_stage = lambda name: {
        "stage": name,
        "touchpoints": [{"claim": f"{name} t", "evidence": [doc_id]}],
        "user_actions": [{"claim": f"{name} u",
                          "evidence": [_doc_id_for("post_002")]}],
        "emotions": [], "frictions": [], "opportunities": [],
    }
    journey_json = json.dumps({"stages": [
        thin_stage("Awareness"),
        rich_stage("Consideration"),
        rich_stage("Decision"),
        rich_stage("Onboarding"),
        rich_stage("Use"),
        rich_stage("Loyalty/Churn"),
    ]})

    persona_client, _ = _make_anthropic_client([_good_persona_json(doc_id)])
    persona, pack, _ = generate_persona(
        c, _POST_TEXTS, _POST_META, region="HK",
        client=persona_client, run_id="r1",
    )
    journey_client, _ = _make_anthropic_client([journey_json])
    journey, _ = generate_journey(persona, pack,
                                  client=journey_client, run_id="r1")
    awareness = next(s for s in journey.stages if s.stage == "Awareness")
    other = next(s for s in journey.stages if s.stage == "Consideration")
    assert awareness.coverage == "thin"
    assert other.coverage == "ok"


# ---------------------------------------------------------------------------
# Cost estimate + cap + dry run
# ---------------------------------------------------------------------------


def test_cost_estimate_under_two_dollars_for_seven_clusters() -> None:
    from src.pipeline.synthesize import AnthropicClient

    clusters = [_cluster([f"post_{i:03d}"] * 22, cluster_id=f"c_{i}")
                for i in range(7)]
    client = AnthropicClient("dry-run")
    est = estimate_cost(clusters, client=client)
    assert est.clusters == 7
    assert est.estimated_usd < 2.00


def test_synthesize_run_dry_run_does_not_call_api() -> None:
    c = _cluster(["post_001", "post_002"], cluster_id="c1")
    # No env var, no mock transport — if any HTTP call leaks, this raises.
    report = synthesize_run(
        topic="MTR Mobile",
        region="HK",
        clusters=[c],
        post_texts=_POST_TEXTS,
        post_metadata=_POST_META,
        provider="anthropic",
        dry_run=True,
    )
    assert report.dry_run is True
    assert report.estimate is not None
    assert report.estimate.clusters == 1
    assert report.personas == []
    assert report.journeys == []


def test_synthesize_run_hard_fails_when_estimate_exceeds_cap(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Build a fake estimate above the cap by setting an absurdly low cap.
    c = _cluster(["post_001"], cluster_id="c1")
    with pytest.raises(CostCapExceeded):
        synthesize_run(
            topic="MTR Mobile",
            region="HK",
            clusters=[c],
            post_texts=_POST_TEXTS,
            post_metadata=_POST_META,
            provider="anthropic",
            max_cost_usd=0.000001,  # essentially zero
        )


def test_synthesize_run_with_force_bypasses_cap(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    c = _cluster(["post_001"], cluster_id="c1")
    doc_id = _doc_id_for("post_001")
    transport = _ScriptedHTTP([
        _claude_reply(_good_persona_json(doc_id)),
        _claude_reply(_good_journey_json(doc_id),
                      input_tokens=80, cache_read_input_tokens=1200),
    ])
    http_client = httpx.Client(transport=httpx.MockTransport(transport))

    report = synthesize_run(
        topic="MTR Mobile",
        region="HK",
        clusters=[c],
        post_texts=_POST_TEXTS,
        post_metadata=_POST_META,
        provider="anthropic",
        max_cost_usd=0.000001,
        force=True,
        http_client=http_client,
    )
    assert len(report.personas) == 1
    assert len(report.journeys) == 1
    assert report.total_cached_input_tokens > 0  # prompt cache hit on journey


def test_missing_api_key_raises_with_clear_message(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = _cluster(["post_001"], cluster_id="c1")
    with pytest.raises(MissingAPIKey) as exc:
        synthesize_run(
            topic="MTR Mobile",
            region="HK",
            clusters=[c],
            post_texts=_POST_TEXTS,
            post_metadata=_POST_META,
            provider="anthropic",
            dry_run=False,
        )
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_missing_deepseek_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    c = _cluster(["post_001"], cluster_id="c1")
    with pytest.raises(MissingAPIKey) as exc:
        synthesize_run(
            topic="MTR Mobile",
            region="HK",
            clusters=[c],
            post_texts=_POST_TEXTS,
            post_metadata=_POST_META,
            provider="deepseek",
            dry_run=False,
        )
    assert "DEEPSEEK_API_KEY" in str(exc.value)


# ---------------------------------------------------------------------------
# DeepSeek backend (response format + pricing)
# ---------------------------------------------------------------------------


def test_deepseek_client_parses_openai_compat_response() -> None:
    doc_id = _doc_id_for("post_001")
    body = {
        "choices": [
            {"message": {"role": "assistant",
                         "content": _good_persona_json(doc_id)}},
        ],
        "usage": {
            "prompt_tokens": 1500,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 1500,
            "completion_tokens": 500,
        },
    }
    transport = _ScriptedHTTP([body])
    http_client = httpx.Client(transport=httpx.MockTransport(transport))
    client = DeepSeekClient("test-key", client=http_client)
    text, usage = client.synthesize(
        rules_block="rules", evidence_block="evidence",
        task_message="task", model="deepseek-chat",
    )
    assert "Frustrated Daily Commuter" in text
    assert usage.input_tokens == 1500
    assert usage.cached_input_tokens == 0
    assert usage.output_tokens == 500


def test_deepseek_cache_hit_reflected_in_usage() -> None:
    doc_id = _doc_id_for("post_001")
    body = {
        "choices": [
            {"message": {"content": _good_persona_json(doc_id)}},
        ],
        "usage": {
            "prompt_cache_hit_tokens": 1200,
            "prompt_cache_miss_tokens": 200,
            "completion_tokens": 400,
        },
    }
    transport = _ScriptedHTTP([body])
    http_client = httpx.Client(transport=httpx.MockTransport(transport))
    client = DeepSeekClient("test-key", client=http_client)
    _, usage = client.synthesize(
        rules_block="r", evidence_block="e", task_message="t",
        model="deepseek-chat",
    )
    assert usage.cached_input_tokens == 1200
    assert usage.input_tokens == 200


def test_deepseek_pricing_is_an_order_of_magnitude_cheaper_than_claude() -> None:
    from src.pipeline.synthesize import AnthropicClient, Usage

    u = Usage(input_tokens=10_000, output_tokens=5_000,
              cached_input_tokens=20_000, cache_write_tokens=10_000)
    claude = AnthropicClient("dry-run").pricing("claude-sonnet-4-6").cost(u)
    deepseek = DeepSeekClient("dry-run").pricing("deepseek-chat").cost(u)
    # Claude should be at least 5x more expensive than DeepSeek for this mix.
    assert claude > 5 * deepseek
