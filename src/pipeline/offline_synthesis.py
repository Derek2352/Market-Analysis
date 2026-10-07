"""Keyless synthesis: export the synthesizer's prompts, import the answers.

``mkt synthesize`` calls Anthropic or DeepSeek, so it needs an API key. This
module runs the *same* synthesis without one: it exports the exact rules,
evidence pack and task the API call would receive, lets a person (or an
in-session assistant) write the JSON answers, checks them with the
synthesizer's own grounding validators, then replays them through the real
``generate_persona`` / ``generate_journey`` so the saved Persona and
JourneyMap are built exactly as an API run would build them.

Workflow (see ``mkt synthesize-offline --help``)::

    export   -> <out>/<target>/{rules,evidence,persona_task}.txt
    (write <target>/persona_response.json)
    validate -> checks it; once valid writes <target>/journey_task.txt
    (write <target>/journey_response.json)
    validate -> checks both
    import   -> writes data/personas/... and data/journeys/...

A target is one cluster, or several clusters merged into one persona
(``--merge cluster_001+cluster_002``) when they describe the same user type.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.pipeline import synthesize as S
from src.schemas.cluster import Cluster, ClusteringResult
from src.util_slug import slugify

MANIFEST = "manifest.json"
PERSONA_RESPONSE = "persona_response.json"
JOURNEY_RESPONSE = "journey_response.json"


class OfflineSynthesisError(Exception):
    """User-facing error: missing files, unknown clusters, invalid answers."""


# ---------------------------------------------------------------------------
# Loading clusters + posts
# ---------------------------------------------------------------------------


@dataclass
class SynthesisInputs:
    result: ClusteringResult
    cluster_run_id: str
    post_texts: dict[str, str]
    post_metadata: dict[str, dict[str, Any]]


def load_inputs(data_dir: Path, topic: str, region: str, cluster_run_id: str | None = None) -> SynthesisInputs:
    """Load a clustering result plus the raw posts it refers to."""
    slug = slugify(topic)
    clusters_dir = data_dir / "clusters" / slug / region
    if cluster_run_id:
        cluster_file = clusters_dir / f"clusters_{cluster_run_id}.json"
    else:
        files = sorted(clusters_dir.glob("clusters_*.json"))
        cluster_file = files[-1] if files else clusters_dir / "clusters_<none>.json"
    if not cluster_file.exists():
        raise OfflineSynthesisError(f"No clustering run at {cluster_file}. Run mkt cluster first.")
    result = ClusteringResult(**json.loads(cluster_file.read_text(encoding="utf-8")))

    post_texts: dict[str, str] = {}
    post_metadata: dict[str, dict[str, Any]] = {}
    for rf in sorted((data_dir / "raw" / slug / region).glob("*.json")):
        if rf.name.endswith("._run.json"):
            continue
        for p in json.loads(rf.read_text(encoding="utf-8")):
            pid = p.get("id", "")
            if not pid:
                continue
            post_texts[pid] = f"{p.get('title', '') or ''}\n{p.get('body', '') or ''}".strip()
            post_metadata[pid] = {
                "source": p.get("source", ""),
                "url": p.get("url", ""),
                "lang": p.get("language_detected", "en"),
                "posted_at": p.get("posted_at"),
            }
    return SynthesisInputs(result, cluster_file.stem.removeprefix("clusters_"), post_texts, post_metadata)


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


def _merge_dist(parts: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in parts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def merge_clusters(parts: list[Cluster]) -> Cluster:
    """Combine clusters that describe one user type into a single target."""
    if len(parts) == 1:
        return parts[0]
    keywords: list[str] = []
    for c in parts:
        keywords += [k for k in c.keyword_summary if k not in keywords]
    reps: list[str] = []
    for i in range(max(len(c.representative_post_ids) for c in parts)):
        reps += [c.representative_post_ids[i] for c in parts if i < len(c.representative_post_ids)]
    post_ids = [pid for c in parts for pid in c.post_ids]
    return Cluster(
        cluster_id="merged_" + "_".join(c.cluster_id.removeprefix("cluster_") for c in parts),
        topic=parts[0].topic,
        region=parts[0].region,
        size=len(post_ids),
        post_ids=post_ids,
        representative_post_ids=reps[:10],
        keyword_summary=keywords[:12],
        source_distribution=_merge_dist([c.source_distribution for c in parts]),
        language_distribution=_merge_dist([c.language_distribution for c in parts]),
        sentiment_distribution=_merge_dist([c.sentiment_distribution for c in parts]),
        temporal_distribution=dict(sorted(_merge_dist([c.temporal_distribution for c in parts]).items())),
        noise_post_count=parts[0].noise_post_count,
        generated_at=parts[0].generated_at,
        params=parts[0].params,
    )


def plan_targets(result: ClusteringResult, groups: list[list[str]]) -> dict[str, Cluster]:
    """Map target name -> cluster. Empty *groups* means one target per cluster."""
    by_id = {c.cluster_id: c for c in result.clusters}
    if not groups:
        groups = [[c.cluster_id] for c in result.clusters]
    targets: dict[str, Cluster] = {}
    for group in groups:
        unknown = [g for g in group if g not in by_id]
        if unknown:
            raise OfflineSynthesisError(
                f"Unknown cluster id(s) {unknown}. Available: {sorted(by_id)}"
            )
        cluster = merge_clusters([by_id[g] for g in group])
        targets[cluster.cluster_id] = cluster
    return targets


# ---------------------------------------------------------------------------
# export / validate / import
# ---------------------------------------------------------------------------


def export(out_dir: Path, inputs: SynthesisInputs, topic: str, region: str, groups: list[list[str]]) -> dict[str, Cluster]:
    targets = plan_targets(inputs.result, groups)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, cluster in targets.items():
        pack = S._build_evidence_pack(cluster, inputs.post_texts, inputs.post_metadata, region)
        tdir = out_dir / name
        tdir.mkdir(exist_ok=True)
        (tdir / "rules.txt").write_text(S.HARD_RULES, encoding="utf-8")
        (tdir / "evidence.txt").write_text(pack.block_text, encoding="utf-8")
        (tdir / "persona_task.txt").write_text(S._persona_task(), encoding="utf-8")
    manifest = {
        "topic": topic,
        "region": region,
        "cluster_run_id": inputs.cluster_run_id,
        "targets": {name: {"cluster_ids": group} for name, group in zip(
            targets, groups or [[c.cluster_id] for c in inputs.result.clusters]
        )},
    }
    (out_dir / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return targets


def _load_manifest(out_dir: Path) -> dict[str, Any]:
    path = out_dir / MANIFEST
    if not path.exists():
        raise OfflineSynthesisError(f"No {MANIFEST} in {out_dir}. Run `mkt synthesize-offline export` first.")
    return json.loads(path.read_text(encoding="utf-8"))


def _rebuild(out_dir: Path, data_dir: Path) -> tuple[dict[str, Any], SynthesisInputs, dict[str, Cluster]]:
    manifest = _load_manifest(out_dir)
    inputs = load_inputs(data_dir, manifest["topic"], manifest["region"], manifest["cluster_run_id"])
    groups = [t["cluster_ids"] for t in manifest["targets"].values()]
    return manifest, inputs, plan_targets(inputs.result, groups)


@dataclass
class TargetStatus:
    name: str
    persona_errors: list[str] | None = None   # None = no response yet
    journey_errors: list[str] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.persona_errors == [] and self.journey_errors == []


def _check(path: Path, validator, pack) -> list[str] | None:
    if not path.exists():
        return None
    parsed = S._extract_json(path.read_text(encoding="utf-8"))
    if parsed is None:
        return ["response is not valid JSON"]
    return validator(parsed, pack)


def validate(out_dir: Path, data_dir: Path) -> list[TargetStatus]:
    """Validate every target's answers; write journey_task.txt once a persona passes."""
    manifest, inputs, targets = _rebuild(out_dir, data_dir)
    statuses: list[TargetStatus] = []
    for name, cluster in targets.items():
        tdir = out_dir / name
        pack = S._build_evidence_pack(cluster, inputs.post_texts, inputs.post_metadata, manifest["region"])
        st = TargetStatus(name)
        st.persona_errors = _check(tdir / PERSONA_RESPONSE, S._validate_grounding_persona, pack)
        if st.persona_errors == []:
            persona = S._extract_json((tdir / PERSONA_RESPONSE).read_text(encoding="utf-8"))
            task = S._journey_task(str(persona.get("name", "")), str(persona.get("one_liner", "")))
            (tdir / "journey_task.txt").write_text(task, encoding="utf-8")
            st.journey_errors = _check(tdir / JOURNEY_RESPONSE, S._validate_grounding_journey, pack)
        statuses.append(st)
    return statuses


class _ReplayClient:
    """LLMClient that answers with pre-written responses instead of an API."""

    name = "offline"
    default_model = "offline"

    def __init__(self, text: str) -> None:
        self._text = text

    def synthesize(self, *, rules_block, evidence_block, task_message, model, max_tokens=0):
        return self._text, S.Usage()

    def pricing(self, model):
        return S._ProviderPricing(0.0, 0.0, 0.0, 0.0)


def import_(out_dir: Path, data_dir: Path, model_label: str = "offline") -> list[tuple[Any, Any]]:
    """Replay validated answers through the real builders and persist them."""
    manifest, inputs, targets = _rebuild(out_dir, data_dir)
    statuses = {s.name: s for s in validate(out_dir, data_dir)}
    not_ready = [n for n, s in statuses.items() if not s.ready]
    if not_ready:
        raise OfflineSynthesisError(
            f"Target(s) {not_ready} are missing or failing validation. "
            "Run `mkt synthesize-offline validate` and fix them first."
        )

    slug = slugify(manifest["topic"])
    region = manifest["region"]
    run_id = manifest["cluster_run_id"]
    personas_dir = data_dir / "personas" / slug / region
    journeys_dir = data_dir / "journeys" / slug / region
    personas_dir.mkdir(parents=True, exist_ok=True)
    journeys_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for name, cluster in targets.items():
        tdir = out_dir / name
        persona_client = _ReplayClient((tdir / PERSONA_RESPONSE).read_text(encoding="utf-8"))
        journey_client = _ReplayClient((tdir / JOURNEY_RESPONSE).read_text(encoding="utf-8"))
        persona, pack, _ = S.generate_persona(
            cluster, inputs.post_texts, inputs.post_metadata, region,
            client=persona_client, model=model_label, run_id=run_id,
        )
        journey, _ = S.generate_journey(persona, pack, client=journey_client, model=model_label, run_id=run_id)
        for obj, folder in ((persona, personas_dir), (journey, journeys_dir)):
            (folder / f"{obj.id}.json").write_text(
                json.dumps(obj.model_dump(mode="json"), indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
        written.append((persona, journey))
    return written
