"""Clustering layer — UMAP + HDBSCAN over bge-m3 embeddings.

Algorithm: UMAP reduces 1024-dim embeddings → ~10 dims, then HDBSCAN clusters.
This is the standard pipeline for opinion clustering — handles variable cluster
sizes, identifies noise/outliers rather than forcing every post into a cluster.

Parameters are configurable via ``configs/clustering.yaml``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from src.schemas.cluster import Cluster, ClusteringResult

_log = structlog.get_logger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "umap": {
        "n_neighbors": 15,
        "min_dist": 0.1,
        "n_components": 10,
        "random_state": 42,
        "metric": "cosine",
    },
    "hdbscan": {
        "min_cluster_size": 15,
        "min_samples": 5,
    },
    "outlier_threshold": 0.3,  # probability below this → noise
}


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load clustering config from YAML file, falling back to defaults."""
    if path and path.exists():
        try:
            import yaml
            with open(path) as f:
                user = yaml.safe_load(f) or {}
            # Deep merge
            merged = DEFAULT_CONFIG.copy()
            for section in ("umap", "hdbscan"):
                if section in user:
                    merged[section] = {**merged[section], **user[section]}
            if "outlier_threshold" in user:
                merged["outlier_threshold"] = user["outlier_threshold"]
            return merged
        except Exception:
            _log.warning("cluster.config_load_failed", path=str(path))
    return DEFAULT_CONFIG.copy()


def cluster_embeddings(
    vectors: np.ndarray,
    post_ids: list[str],
    topic: str,
    region: str,
    *,
    config: dict[str, Any] | None = None,
    source_map: dict[str, str] | None = None,
    lang_map: dict[str, str] | None = None,
    sentiment_map: dict[str, int] | None = None,
    temporal_map: dict[str, str] | None = None,
    post_texts: dict[str, str] | None = None,
    tokenizer: "object | None" = None,
) -> ClusteringResult:
    """Cluster embeddings using UMAP → HDBSCAN.

    Parameters
    ----------
    vectors:
        (N, 1024) numpy array of normalized embeddings.
    post_ids:
        List of post IDs, aligned with vectors.
    topic, region:
        Metadata for the result.
    config:
        Clustering config dict (from ``load_config()``).
    source_map, lang_map, sentiment_map, temporal_map:
        Per-post metadata maps for building cluster distributions.
    tokenizer:
        Optional ``Tokenizer`` from ``src.lang`` for region-aware
        c-TF-IDF keyword extraction (CJK tokenization).
    """
    cfg = config or DEFAULT_CONFIG
    n = vectors.shape[0]
    _log.info("cluster.start", posts=n, topic=topic, region=region)

    # Guard: datasets too small for UMAP+HDBSCAN → single cluster
    min_cluster = cfg["hdbscan"]["min_cluster_size"]
    if n < min_cluster:
        _log.info("cluster.small_dataset", posts=n, min_cluster=min_cluster)
        cluster_ids = list(post_ids)
        cluster = Cluster(
            cluster_id="single",
            topic=topic,
            region=region,
            size=n,
            post_ids=cluster_ids,
            representative_post_ids=cluster_ids[:5],
            keyword_summary=[],
            source_distribution=_build_distribution(cluster_ids, source_map) if source_map else {},
            language_distribution=_build_distribution(cluster_ids, lang_map) if lang_map else {},
            sentiment_distribution=_build_distribution(cluster_ids, sentiment_map) if sentiment_map else {},
            temporal_distribution=_build_distribution(cluster_ids, temporal_map) if temporal_map else {},
            generated_at=datetime.now(timezone.utc),
        )
        return ClusteringResult(
            topic=topic,
            region=region,
            total_posts=n,
            noise_count=0,
            clusters=[cluster],
            params=cfg,
            generated_at=datetime.now(timezone.utc),
        )

    # 1. UMAP dimensionality reduction
    umap_cfg = cfg["umap"]
    reduced = _run_umap(vectors, umap_cfg)

    # 2. HDBSCAN clustering
    hdbscan_cfg = cfg["hdbscan"]
    labels, probs = _run_hdbscan(reduced, hdbscan_cfg)

    # 3. Mark low-confidence assignments as noise
    outlier_threshold = cfg["outlier_threshold"]
    labels[probs < outlier_threshold] = -1

    # 4. Build clusters
    unique_labels = sorted(set(labels) - {-1})
    clusters: list[Cluster] = []

    for lbl in unique_labels:
        mask = labels == lbl
        cluster_ids = [post_ids[i] for i in range(n) if mask[i]]
        cluster_vectors = vectors[mask]

        centroid = cluster_vectors.mean(axis=0)

        # Representative posts — 5 closest to centroid
        distances = np.linalg.norm(cluster_vectors - centroid, axis=1)
        top5_idx = np.argsort(distances)[:5]
        representative = [cluster_ids[i] for i in top5_idx]

        # Keyword summary via c-TF-IDF (real computation if texts available)
        if post_texts:
            keywords = _compute_ctfidf_for_cluster(
                cluster_ids, post_texts, all_ids=post_ids, top_n=10,
                tokenizer=tokenizer,
            )
        else:
            keywords = [f"cluster_{lbl}" for _ in range(min(10, len(cluster_ids)))]

        # Distributions
        src_dist = _build_distribution(cluster_ids, source_map)
        lang_dist = _build_distribution(cluster_ids, lang_map)
        sent_dist = _build_distribution(cluster_ids, sentiment_map)
        temp_dist = _build_distribution(cluster_ids, temporal_map)

        cluster = Cluster(
            cluster_id=f"cluster_{lbl:03d}",
            topic=topic,
            region=region,
            size=len(cluster_ids),
            post_ids=cluster_ids,
            representative_post_ids=representative,
            keyword_summary=keywords,
            source_distribution=src_dist,
            language_distribution=lang_dist,
            sentiment_distribution=sent_dist,
            temporal_distribution=temp_dist,
            noise_post_count=int(np.sum(labels == -1)),
            generated_at=datetime.now(timezone.utc),
            params=cfg,
        )
        clusters.append(cluster)

    noise_count = int(np.sum(labels == -1))

    _log.info(
        "cluster.done",
        clusters=len(clusters),
        noise=noise_count,
        noise_pct=round(noise_count / n * 100, 1) if n else 0,
    )

    return ClusteringResult(
        topic=topic,
        region=region,
        total_posts=n,
        noise_count=noise_count,
        clusters=clusters,
        params=cfg,
        generated_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _run_umap(vectors: np.ndarray, config: dict) -> np.ndarray:
    """Reduce dimensionality with UMAP."""
    try:
        import umap
    except ImportError:
        raise RuntimeError("umap-learn not installed. Install with: pip install umap-learn")

    reducer = umap.UMAP(
        n_neighbors=config["n_neighbors"],
        min_dist=config["min_dist"],
        n_components=min(config["n_components"], vectors.shape[0] - 1),
        random_state=config.get("random_state", 42),
        metric=config.get("metric", "cosine"),
        verbose=False,
    )
    return reducer.fit_transform(vectors)


def _run_hdbscan(
    reduced: np.ndarray, config: dict
) -> tuple[np.ndarray, np.ndarray]:
    """Cluster reduced vectors with HDBSCAN. Returns (labels, probabilities)."""
    try:
        import hdbscan
    except ImportError:
        raise RuntimeError("hdbscan not installed. Install with: pip install hdbscan")

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=config["min_cluster_size"],
        min_samples=config["min_samples"],
        metric="euclidean",  # UMAP space is Euclidean
        core_dist_n_jobs=1,
    )
    labels = clusterer.fit_predict(reduced)

    # Get probabilities (soft clustering)
    try:
        probs = hdbscan.all_points_membership_vectors(clusterer)
        # Take max probability per point as confidence
        confidences = probs.max(axis=1)
    except Exception:
        # Fallback: high confidence for clustered, low for noise
        confidences = np.where(labels == -1, 0.0, 0.8)

    return labels, confidences


def _build_distribution(
    ids: list[str], mapping: dict[str, Any] | None
) -> dict[str, int]:
    """Count occurrences of each value in *mapping* for the given *ids*."""
    if not mapping:
        return {}
    dist: dict[str, int] = {}
    for pid in ids:
        val = mapping.get(pid)
        if val is not None:
            key = str(val)
            dist[key] = dist.get(key, 0) + 1
    return dict(sorted(dist.items(), key=lambda x: -x[1]))


def _compute_ctfidf_for_cluster(
    cluster_ids: list[str],
    post_texts: dict[str, str],
    all_ids: list[str],
    top_n: int = 10,
    *,
    tokenizer: "object | None" = None,
) -> list[str]:
    """Compute c-TF-IDF keywords for one cluster vs all other posts.

    Treats this cluster's text as one document, all other posts as another,
    then computes TF-IDF to find words distinctive to this cluster.

    When *tokenizer* is provided (a ``Tokenizer`` from ``src.lang``), text is
    pre-tokenized before TfidfVectorizer, giving region-appropriate keyword
    extraction for CJK languages. When None, uses English defaults.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer

    def _prepare(text: str) -> str:
        if tokenizer is not None:
            return " ".join(tokenizer.tokenize(text))
        return text

    cluster_doc = _prepare(" ".join(post_texts.get(pid, "") for pid in cluster_ids))
    other_ids = [pid for pid in all_ids if pid not in set(cluster_ids)]
    other_doc = _prepare(" ".join(post_texts.get(pid, "") for pid in other_ids)) if other_ids else ""

    docs = [cluster_doc, other_doc] if other_doc else [cluster_doc]

    if tokenizer is not None:
        # Already tokenized — use identity analyzer
        vectorizer = TfidfVectorizer(
            max_features=1000,
            tokenizer=lambda x: x.split(),
            lowercase=False,
            ngram_range=(1, 2),
        )
    else:
        vectorizer = TfidfVectorizer(
            max_features=1000, stop_words="english", ngram_range=(1, 2),
        )

    tfidf = vectorizer.fit_transform(docs)
    names = vectorizer.get_feature_names_out()
    row = tfidf[0].toarray().flatten()
    top = row.argsort()[-top_n:][::-1]
    return [names[i] for i in top]
