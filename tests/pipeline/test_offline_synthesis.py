"""mkt synthesize-offline: export -> validate -> import, end to end, no API."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.pipeline import offline_synthesis as O
from src.pipeline import synthesize as S
from src.schemas.synthesis import JourneyMap, Persona

TEXTS = {
    "p1": ("登入唔到", "更新完又登入唔到，俾錢都唔得"),
    "p2": ("驗證碼", "驗證碼只可以輸入第一格"),
    "p3": ("會員", "明明係會員又話唔係會員"),
}


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    raw = d / "raw" / "demo_app" / "HK"
    raw.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    raw.joinpath("app_store_hk_R1.json").write_text(json.dumps([
        {"id": pid, "source": "app_store_hk", "url": "https://apps.apple.com/hk/app/id1",
         "language_detected": "yue", "posted_at": now, "title": t, "body": b,
         "engagement_metrics": {"rating": 1}}
        for pid, (t, b) in TEXTS.items()
    ], ensure_ascii=False), encoding="utf-8")
    clusters = d / "clusters" / "demo_app" / "HK"
    clusters.mkdir(parents=True)
    def cl(cid, ids):
        return {"cluster_id": cid, "topic": "Demo App", "region": "HK", "size": len(ids),
                "post_ids": ids, "representative_post_ids": ids, "keyword_summary": ["登入"],
                "source_distribution": {"app_store_hk": len(ids)}}
    clusters.joinpath("clusters_R1.json").write_text(json.dumps({
        "topic": "Demo App", "region": "HK", "total_posts": 3, "noise_count": 0,
        "clusters": [cl("cluster_000", ["p1", "p2"]), cl("cluster_001", ["p3"])],
    }), encoding="utf-8")
    return d


def _doc(pid):
    return S._doc_id_for(pid)


def _persona(cite):
    claim = lambda text: [{"claim": text, "evidence": [cite("p1")]}]
    return {
        "name": "Locked-out member", "one_liner": "Can't get into the app.",
        "demographics": {"age_range": "20-40", "occupation_examples": ["worker"], "evidence": [cite("p1")]},
        "goals": claim("Log in"), "motivations": claim("Collect points"),
        "pain_points": [{"claim": "Login fails after update", "severity": "high", "evidence": [cite("p1")]}],
        "preferred_channels": claim("The app"), "behaviors": claim("Retries login"),
        "representative_quotes": [
            {"text_original": "更新完又登入唔到", "lang": "yue", "doc_id": _doc("p1")},
            {"text_original": "驗證碼只可以輸入第一格", "lang": "yue", "doc_id": _doc("p2")},
            {"text_original": "明明係會員又話唔係會員", "lang": "yue", "doc_id": _doc("p3")},
        ],
    }


def _journey():
    ev = [_doc("p1"), _doc("p2")]
    stage = lambda name: {"stage": name,
        "touchpoints": [{"claim": "App", "evidence": ev}],
        "user_actions": [{"claim": "Tries to log in", "evidence": ev}],
        "emotions": [{"label": "frustrated", "intensity": 0.8, "evidence": ev}],
        "frictions": [{"claim": "Login fails", "evidence": ev}],
        "opportunities": [{"claim": "Fix login", "evidence": ev}]}
    return {"stages": [stage(s) for s in S.JOURNEY_STAGES]}


def test_export_validate_import_roundtrip(data_dir, tmp_path):
    out = tmp_path / "bundle"
    inputs = O.load_inputs(data_dir, "Demo App", "HK")
    targets = O.export(out, inputs, "Demo App", "HK", [["cluster_000", "cluster_001"]])
    (name,) = targets
    assert targets[name].size == 3
    tdir = out / name
    assert {"rules.txt", "evidence.txt", "persona_task.txt"} <= {p.name for p in tdir.iterdir()}

    # No answer yet.
    (st,) = O.validate(out, data_dir)
    assert st.persona_errors is None and not st.ready

    # Ungrounded answer: cites a doc that isn't in the pack.
    (tdir / O.PERSONA_RESPONSE).write_text(json.dumps(_persona(lambda p: "doc_bogus")), encoding="utf-8")
    (st,) = O.validate(out, data_dir)
    assert st.persona_errors and "doc_bogus" in " ".join(st.persona_errors)
    with pytest.raises(O.OfflineSynthesisError):
        O.import_(out, data_dir)

    # Grounded answer unlocks the journey task.
    (tdir / O.PERSONA_RESPONSE).write_text(json.dumps(_persona(_doc), ensure_ascii=False), encoding="utf-8")
    (st,) = O.validate(out, data_dir)
    assert st.persona_errors == [] and st.journey_errors is None
    assert "Locked-out member" in (tdir / "journey_task.txt").read_text(encoding="utf-8")

    (tdir / O.JOURNEY_RESPONSE).write_text(json.dumps(_journey(), ensure_ascii=False), encoding="utf-8")
    (st,) = O.validate(out, data_dir)
    assert st.ready

    ((persona, journey),) = O.import_(out, data_dir)
    saved_p = data_dir / "personas" / "demo_app" / "HK" / f"{persona.id}.json"
    saved_j = data_dir / "journeys" / "demo_app" / "HK" / f"{journey.id}.json"
    p = Persona(**json.loads(saved_p.read_text(encoding="utf-8")))
    j = JourneyMap(**json.loads(saved_j.read_text(encoding="utf-8")))
    assert p.run_id == "R1" and j.persona_id == p.id
    assert p.confidence == 0.6           # single-perspective evidence is capped
    assert len(p.representative_quotes) == 3


def test_unknown_cluster_is_a_clear_error(data_dir, tmp_path):
    inputs = O.load_inputs(data_dir, "Demo App", "HK")
    with pytest.raises(O.OfflineSynthesisError, match="cluster_999"):
        O.export(tmp_path / "b", inputs, "Demo App", "HK", [["cluster_999"]])


def test_merge_combines_distributions(data_dir):
    inputs = O.load_inputs(data_dir, "Demo App", "HK")
    merged = O.merge_clusters(inputs.result.clusters)
    assert merged.size == 3
    assert merged.source_distribution == {"app_store_hk": 3}
    assert merged.cluster_id == "merged_000_001"
