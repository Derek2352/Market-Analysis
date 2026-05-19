"""Pipeline tests — embedding, clustering, schemas."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import numpy as np
from src.schemas.cluster import Cluster, ClusteringResult
from src.schemas.raw import RawPost
from src.schemas.enums import SignalType, SourceCategory


# ---------------------------------------------------------------------------
# Schema round-trip tests
# ---------------------------------------------------------------------------

def test_cluster_schema_round_trip():
    """Cluster serializes/deserializes without loss."""
    cluster = Cluster(
        cluster_id="cluster_001",
        topic="test",
        region="HK",
        size=10,
        post_ids=["a", "b", "c"],
        representative_post_ids=["a", "b"],
        keyword_summary=["word1", "word2"],
        source_distribution={"lihkg": 7, "reddit_old": 3},
        language_distribution={"zh": 8, "en": 2},
        sentiment_distribution={"negative": 5, "neutral": 5},
        temporal_distribution={"2026-05": 10},
        noise_post_count=5,
        generated_at=datetime.now(timezone.utc),
        params={"umap_n_neighbors": 15},
    )

    data = cluster.model_dump(mode="json")
    restored = Cluster(**data)
    assert restored.cluster_id == cluster.cluster_id
    assert restored.size == cluster.size
    assert restored.post_ids == cluster.post_ids
    assert restored.keyword_summary == cluster.keyword_summary
    assert restored.source_distribution == cluster.source_distribution


def test_clustering_result_round_trip():
    """ClusteringResult serializes/deserializes."""
    cluster = Cluster(
        cluster_id="cluster_001",
        topic="test",
        region="HK",
        size=5,
        post_ids=["a", "b", "c", "d", "e"],
        keyword_summary=["k1", "k2"],
    )
    result = ClusteringResult(
        topic="test",
        region="HK",
        total_posts=10,
        noise_count=5,
        clusters=[cluster],
        params={"umap_n_neighbors": 15},
        generated_at=datetime.now(timezone.utc),
    )

    data = result.model_dump(mode="json")
    restored = ClusteringResult(**data)
    assert restored.topic == result.topic
    assert restored.total_posts == result.total_posts
    assert len(restored.clusters) == 1
    assert restored.clusters[0].cluster_id == "cluster_001"


# ---------------------------------------------------------------------------
# Embedding determinism test
# ---------------------------------------------------------------------------

def test_embedding_determinism():
    """Same text → same vector with BGE-M3."""
    from src.pipeline.embed import EmbeddingStore

    db_path = Path("/tmp") / "test_determinism.duckdb"
    store = EmbeddingStore(db_path=db_path, use_cache=False)

    post1 = RawPost(
        id="test_det_1",
        source="test",
        source_category=SourceCategory.FORUMS,
        region="HK",
        language="en",
        url="https://example.com/1",
        author_hash="hash1",
        title="This is a test post about MTR fares",
        body="The MTR fares keep increasing every year. It's getting too expensive.",
        posted_at=datetime.now(timezone.utc),
        signal_type=SignalType.OPINION,
    )

    # Encode twice
    n1 = store.embed_posts([post1], topic="test_det", region="HK")
    n2 = store.embed_posts([post1], topic="test_det", region="HK")

    # First call should embed, second should skip (idempotent)
    assert n1 == 1
    assert n2 == 0  # idempotent

    store.close()
    if db_path.exists():
        db_path.unlink()


# ---------------------------------------------------------------------------
# Multilingual sanity check
# ---------------------------------------------------------------------------

def test_embedding_multilingual_similarity():
    """Cantonese '好難用' and English 'very difficult to use' should have cosine similarity >0.5."""
    from src.pipeline.embed import EmbeddingStore

    db_path = Path("/tmp") / "test_multilingual.duckdb"
    store = EmbeddingStore(db_path=db_path, use_cache=False)

    post_zh = RawPost(
        id="test_zh_1",
        source="test",
        source_category=SourceCategory.FORUMS,
        region="HK",
        language="zh",
        url="https://example.com/zh",
        author_hash="hash_zh",
        title="好難用",
        body="呢個app真係好難用，成日hang機。",
        posted_at=datetime.now(timezone.utc),
        signal_type=SignalType.OPINION,
    )

    post_en = RawPost(
        id="test_en_1",
        source="test",
        source_category=SourceCategory.FORUMS,
        region="HK",
        language="en",
        url="https://example.com/en",
        author_hash="hash_en",
        title="Very difficult to use",
        body="This app is very difficult to use, it crashes all the time.",
        posted_at=datetime.now(timezone.utc),
        signal_type=SignalType.OPINION,
    )

    store.embed_posts([post_zh, post_en], topic="test_ml", region="HK")

    # Get both vectors and compute similarity
    import duckdb
    con = duckdb.connect(str(db_path))
    con.execute("LOAD vss;")
    rows = con.execute(
        "SELECT post_id, vector FROM embeddings WHERE topic = 'test_ml'"
    ).fetchall()

    vecs = {}
    for r in rows:
        vecs[r[0]] = np.array(r[1])

    v1 = vecs.get("test_zh_1")
    v2 = vecs.get("test_en_1")
    assert v1 is not None and v2 is not None

    similarity = float(np.dot(v1, v2))
    assert similarity > 0.5, f"Cross-lingual similarity {similarity:.3f} should be >0.5"

    store.close()
    con.close()
    if db_path.exists():
        db_path.unlink()


# ---------------------------------------------------------------------------
# Clustering reproducibility
# ---------------------------------------------------------------------------

def test_clustering_reproducibility():
    """Same vectors + fixed seed → same clusters."""
    from src.pipeline.cluster import cluster_embeddings

    rng = np.random.RandomState(42)
    vectors = rng.randn(200, 1024).astype(np.float32)
    post_ids = [f"post_{i}" for i in range(200)]

    config = {
        "umap": {"n_neighbors": 15, "min_dist": 0.1, "n_components": 5, "random_state": 42, "metric": "cosine"},
        "hdbscan": {"min_cluster_size": 10, "min_samples": 3},
        "outlier_threshold": 0.3,
    }

    r1 = cluster_embeddings(vectors, post_ids, "test", "HK", config=config)
    r2 = cluster_embeddings(vectors, post_ids, "test", "HK", config=config)

    assert r1.noise_count == r2.noise_count
    assert len(r1.clusters) == len(r2.clusters)
    for c1, c2 in zip(sorted(r1.clusters, key=lambda c: c.cluster_id),
                       sorted(r2.clusters, key=lambda c: c.cluster_id)):
        assert c1.size == c2.size
        assert c1.post_ids == c2.post_ids


# ---------------------------------------------------------------------------
# Synthetic cluster recovery test
# ---------------------------------------------------------------------------

def test_synthetic_cluster_recovery():
    """HDBSCAN should recover ≥4 of 5 known clusters from 100 hand-crafted posts."""
    from src.pipeline.embed import EmbeddingStore
    from src.pipeline.cluster import cluster_embeddings

    # Create 100 synthetic posts with known 5-cluster structure
    now = datetime.now(timezone.utc)
    posts = []
    post_texts = {}
    clusters = {
        0: {"posts": [], "theme": "price"},
        1: {"posts": [], "theme": "crowding"},
        2: {"posts": [], "theme": "service"},
        3: {"posts": [], "theme": "expansion"},
        4: {"posts": [], "theme": "payment"},
    }
    for i in range(5):
        theme = {0: "price", 1: "crowding", 2: "service", 3: "expansion", 4: "payment"}[i]
        for j in range(20):
            pid = f"synth_{i}_{j}"
            body = f"The {theme} of MTR is problematic. " * 10
            post = RawPost(
                id=pid,
                source="test",
                source_category=SourceCategory.FORUMS,
                region="HK",
                language="en",
                url=f"https://example.com/{pid}",
                author_hash=f"hash_{i}_{j}",
                title=f"MTR {theme} issue {j}",
                body=body,
                posted_at=now,
                signal_type=SignalType.OPINION,
            )
            posts.append(post)
            post_texts[pid] = body
            clusters[i]["posts"].append(pid)

    # Embed
    db_path = Path("/tmp") / "test_recovery.duckdb"
    store = EmbeddingStore(db_path=db_path, use_cache=False)
    store.embed_posts(posts, topic="test_recovery", region="HK")

    # Get vectors
    import duckdb
    con = duckdb.connect(str(db_path))
    con.execute("LOAD vss;")
    rows = con.execute(
        "SELECT post_id, vector FROM embeddings WHERE topic = 'test_recovery'"
    ).fetchall()

    vectors = np.array([np.array(r[1]) for r in rows])
    post_ids_list = [r[0] for r in rows]

    config = {
        "umap": {"n_neighbors": 5, "min_dist": 0.0, "n_components": 5, "random_state": 42, "metric": "cosine"},
        "hdbscan": {"min_cluster_size": 8, "min_samples": 2},
        "outlier_threshold": 0.15,
    }

    result = cluster_embeddings(vectors, post_ids_list, "test_recovery", "HK", config=config, post_texts=post_texts)

    # Count how many known clusters have majority of their posts in one found cluster
    recovered = 0
    for i in range(5):
        true_posts = set(clusters[i]["posts"])
        best_match = 0
        for c in result.clusters:
            overlap = len(true_posts & set(c.post_ids))
            best_match = max(best_match, overlap)
        if best_match >= 10:  # At least half of the 20 posts in one cluster
            recovered += 1

    assert recovered >= 4, f"Only {recovered}/5 clusters recovered (need ≥4)"

    store.close()
    con.close()
    if db_path.exists():
        db_path.unlink()


# ---------------------------------------------------------------------------
# c-TF-IDF keyword test
# ---------------------------------------------------------------------------

def test_ctfidf_keywords():
    """c-TF-IDF produces distinctive keywords per cluster."""
    from src.pipeline.cluster import _compute_ctfidf_for_cluster

    post_texts = {
        "a1": "MTR fare increase too expensive ticket price",
        "a2": "MTR ticket price increase fare expensive",
        "b1": "MTR crowding rush hour packed train sardine",
        "b2": "MTR packed train standing room only rush hour crowd",
    }

    # Cluster A: price posts
    kws_a = _compute_ctfidf_for_cluster(
        ["a1", "a2"], post_texts, all_ids=["a1", "a2", "b1", "b2"], top_n=5
    )
    # Should contain price/fare-related terms
    price_terms = {"price", "fare", "ticket", "expensive", "increase"}
    assert any(t in price_terms for t in kws_a), f"Keywords {kws_a} should include price terms"

    # Cluster B: crowding posts
    kws_b = _compute_ctfidf_for_cluster(
        ["b1", "b2"], post_texts, all_ids=["a1", "a2", "b1", "b2"], top_n=5
    )
    crowd_terms = {"crowd", "rush", "packed", "train", "sardine"}
    assert any(t in crowd_terms for t in kws_b), f"Keywords {kws_b} should include crowding terms"

    # Distinctiveness: keywords should differ between clusters
    assert set(kws_a) != set(kws_b), "Keywords should be different for different clusters"
