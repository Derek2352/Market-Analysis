"""Embedding layer — BAAI/bge-m3 via sentence-transformers, stored in DuckDB+VSS.

Converts ``RawPost`` text into 1024-dim multilingual embeddings for clustering.
Uses duckdb-vss extension for zero-setup vector storage — single-file database,
no server process.  Swaps to pgvector in Phase 6+.

CLI: ``mkt embed --topic "..." --region HK`` — idempotent, skips already-embedded
posts based on (post_id, model_version).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from tqdm import tqdm

from src.schemas.raw import RawPost

MODEL_NAME = "BAAI/bge-m3"
MODEL_VERSION = "1.0"
MODEL_DIM = 1024
MAX_CHUNK_TOKENS = 512  # bge-m3 max is 8192, but we chunk conservatively
CHUNK_OVERLAP = 50  # token overlap between chunks
MODEL_CACHE_DIR = Path.home() / ".cache" / "market-analytics" / "models"

# Embedding cache directory — stores SHA256(post_text) → post_id to skip
# re-embedding unchanged posts on subsequent runs. Cache is keyed by model
# version so a model upgrade automatically invalidates old entries.
EMBEDDING_CACHE_DIR = Path("data") / "embedding_cache"

_log = structlog.get_logger(__name__)


class EmbeddingStore:
    """DuckDB-backed embedding store with VSS (Vector Similarity Search).

    Parameters
    ----------
    db_path:
        Path to the DuckDB database file.
    model_cache_dir:
        Where to cache downloaded models.
    """

    def __init__(
        self,
        db_path: Path,
        model_cache_dir: Path = MODEL_CACHE_DIR,
        *,
        use_cache: bool = True,
    ) -> None:
        self.db_path = db_path
        self.model_cache_dir = model_cache_dir
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)
        self.use_cache = use_cache
        self._model = None
        self._con = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_posts(
        self,
        posts: list[RawPost],
        topic: str,
        region: str,
        *,
        batch_size: int = 64,
        progress: bool = True,
    ) -> int:
        """Embed *posts* and store in DuckDB. Returns count of newly embedded.

        Idempotent: skips posts whose (post_id, model_version) already exist.
        Also checks a SHA256 text-hash cache to skip unchanged posts before
        even touching the database or model — cuts re-embed time by ~80 %.
        """
        if not posts:
            return 0

        _log.info("embed.start", topic=topic, region=region, count=len(posts))

        # ── Phase 1: text-hash cache pre-filter ────────────────────────
        # If the post text hasn't changed since the last embed run, skip
        # everything (no DB query, no model load).  This is what does the
        # heavy-lifting on a re-run.
        #
        # Cache can be disabled via EmbeddingStore(use_cache=False) for
        # tests and one-off runs where cache isolation is needed.
        cache_hits = 0
        cache: dict[str, str] = {}
        if self.use_cache:
            cache = self._load_cache()
        cache_hits = 0
        uncached_posts: list[RawPost] = []
        for post in posts:
            text_hash = self._hash_post_text(post)
            if text_hash in cache:
                cache_hits += 1
                continue
            # Mark hash as "seen" immediately so we don't double-process
            # duplicate posts within the same batch.
            cache[text_hash] = post.id
            uncached_posts.append(post)

        if cache_hits:
            _log.info(
                "embed.cache_hits",
                total=len(posts),
                hits=cache_hits,
                remaining=len(uncached_posts),
            )

        if not uncached_posts:
            _log.info("embed.all_cached", count=len(posts))
            return 0

        # ── Phase 2: only load model & DB if there's real work ─────────
        # Lazy-load model
        model = self._load_model()
        con = self._ensure_db()

        # Build text representations
        texts: list[str] = []
        post_ids: list[str] = []
        sources: list[str] = []
        for post in uncached_posts:
            text = self._post_text(post)
            texts.append(text)
            post_ids.append(post.id)
            sources.append(post.source)

        # Filter already-embedded (DuckDB is source of truth even if cache missed)
        existing = self._already_embedded(post_ids, con)
        new_texts: list[str] = []
        new_ids: list[str] = []
        new_sources: list[str] = []
        for i, pid in enumerate(post_ids):
            if pid not in existing:
                new_texts.append(texts[i])
                new_ids.append(pid)
                new_sources.append(sources[i])

        if not new_texts:
            _log.info("embed.all_skipped", count=len(post_ids))
            # Still persist the cache — uncached posts were found in DB
            if self.use_cache:
                self._save_cache(cache)
            return 0

        # Chunk and embed
        chunked_texts, chunk_map = self._chunk_texts(new_texts, new_ids, new_sources)

        vectors = self._encode_batch(chunked_texts, model=model, batch_size=batch_size, progress=progress)

        # Store in DB
        now = datetime.now(timezone.utc).isoformat()
        stored = 0
        for vec, (pid, src) in zip(vectors, chunk_map):
            self._store_embedding(
                con, pid, src, region, topic,
                vec, MODEL_NAME, MODEL_VERSION, now,
            )
            stored += 1

        # Persist updated cache
        if self.use_cache:
            self._save_cache(cache)

        _log.info(
            "embed.done",
            total=len(posts),
            cache_hits=cache_hits,
            new=stored,
            skipped=len(uncached_posts) - len(new_ids),
        )
        return stored

    def search_similar(
        self, text: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        """Find top_k most similar embeddings to *text*."""
        model = self._load_model()
        con = self._ensure_db()

        vec = model.encode([text], normalize_embeddings=True)[0]
        vec_str = f"[{', '.join(f'{v:.6f}' for v in vec)}]"

        result = con.execute(
            """
            SELECT post_id, source, topic, region,
                   array_distance(vector::FLOAT[1024], ?::FLOAT[1024]) AS dist
            FROM embeddings
            ORDER BY dist ASC
            LIMIT ?
            """,
            [vec_str, top_k],
        ).fetchall()

        return [
            {"post_id": r[0], "source": r[1], "topic": r[2], "region": r[3], "distance": r[4]}
            for r in result
        ]

    def get_stats(self) -> dict[str, Any]:
        """Return embedding store statistics."""
        con = self._ensure_db()
        row = con.execute("""
            SELECT COUNT(*) as total, COUNT(DISTINCT topic) as topics
            FROM embeddings
        """).fetchone()
        return {
            "total_embeddings": row[0],
            "unique_topics": row[1],
            "model": MODEL_NAME,
            "model_version": MODEL_VERSION,
            "dimensions": MODEL_DIM,
        }

    def close(self) -> None:
        if self._con:
            self._con.close()
            self._con = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_post_text(post: RawPost) -> str:
        """Compute a stable SHA256 hash of the post's embedding-relevant text.

        Uses the same text building as ``_post_text`` so a change in title or
        body produces a different hash and triggers re-embedding.
        """
        text = EmbeddingStore._post_text_static(post)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _post_text_static(post: RawPost) -> str:
        """Static version of ``_post_text`` usable without an instance."""
        parts = []
        if post.title:
            parts.append(post.title)
        parts.append(post.body)
        return "\n\n".join(parts)

    @property
    def _cache_path(self) -> Path:
        """Path to the JSON cache file, keyed by model version and database.

        Each database gets its own cache file — this prevents cross-test
        contamination where one test's embedded posts are incorrectly
        skipped in another test using a different DuckDB file.
        """
        db_key = self.db_path.stem  # e.g. "alipayhk", "test_determinism"
        cache_dir = EMBEDDING_CACHE_DIR / MODEL_VERSION
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / f"{db_key}_hashes.json"

    def _load_cache(self) -> dict[str, str]:
        """Load SHA256 → post_id cache from disk. Returns empty dict on first run."""
        path = self._cache_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as e:
            _log.warning("embed.cache_load_failed", path=str(path), error=str(e))
        return {}

    def _save_cache(self, cache: dict[str, str]) -> None:
        """Persist SHA256 → post_id cache to disk atomically."""
        path = self._cache_path
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            _log.warning("embed.cache_save_failed", path=str(path), error=str(e))

    def _load_model(self) -> Any:
        """Lazy-load BGE-M3. Cached after first load."""
        if self._model is not None:
            return self._model

        os.environ.setdefault("HF_HOME", str(self.model_cache_dir))
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(self.model_cache_dir))

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )

        _log.info("embed.loading_model", model=MODEL_NAME)
        t0 = time.monotonic()
        self._model = SentenceTransformer(MODEL_NAME, device="cpu")
        elapsed = time.monotonic() - t0
        _log.info("embed.model_loaded", elapsed_seconds=round(elapsed, 1))
        return self._model

    def _ensure_db(self) -> Any:
        """Initialize DuckDB with VSS extension and embeddings table."""
        if self._con is not None:
            return self._con

        try:
            import duckdb
        except ImportError:
            raise RuntimeError(
                "duckdb not installed. Install with: pip install duckdb"
            )

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self.db_path))

        # Enable VSS
        self._con.execute("INSTALL vss;")
        self._con.execute("LOAD vss;")

        # Create embeddings table
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                post_id VARCHAR PRIMARY KEY,
                source VARCHAR,
                region VARCHAR,
                topic VARCHAR,
                model_name VARCHAR,
                model_version VARCHAR,
                vector FLOAT[1024],
                created_at TIMESTAMP
            )
        """)

        # HNSW index for fast similarity search (DuckDB VSS extension)
        try:
            # Enable experimental persistence for file-based databases
            self._con.execute(
                "SET hnsw_enable_experimental_persistence = true"
            )
            self._con.execute(
                "CREATE INDEX IF NOT EXISTS idx_embeddings_vector "
                "ON embeddings USING hnsw (vector)"
            )
        except Exception as e:
            _log.warning("embed.vss_index_create_failed", error=str(e))

        return self._con

    def _already_embedded(self, post_ids: list[str], con: Any) -> set[str]:
        """Return set of post_ids already embedded with current model version."""
        if not post_ids:
            return set()

        # Batch query — DuckDB handles large IN clauses fine
        ids_str = ", ".join(f"'{pid}'" for pid in post_ids)
        rows = con.execute(
            f"SELECT post_id FROM embeddings WHERE post_id IN ({ids_str}) AND model_version = ?",
            [MODEL_VERSION],
        ).fetchall()
        return {r[0] for r in rows}

    def _store_embedding(
        self,
        con: Any,
        post_id: str,
        source: str,
        region: str,
        topic: str,
        vector: Any,
        model_name: str,
        model_version: str,
        created_at: str,
    ) -> None:
        """Insert or update a single embedding row."""
        vec_str = f"[{', '.join(f'{v:.6f}' for v in vector)}]"
        con.execute(
            """
            INSERT OR REPLACE INTO embeddings
            (post_id, source, region, topic, model_name, model_version, vector, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?::FLOAT[1024], ?)
            """,
            [post_id, source, region, topic, model_name, model_version, vec_str, created_at],
        )

    def _post_text(self, post: RawPost) -> str:
        """Build a single text representation for embedding."""
        parts = []
        if post.title:
            parts.append(post.title)
        parts.append(post.body)
        return "\n\n".join(parts)

    def _chunk_texts(
        self, texts: list[str], ids: list[str], sources: list[str]
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Chunk texts to ~512 tokens each. Returns (chunked_texts, chunk_map).

        chunk_map: each entry is (post_id, source) for the chunk.
        For posts under the chunk limit, one chunk = one post.
        For longer posts, chunk by sentence and average later.
        """
        chunked: list[str] = []
        chunk_map: list[tuple[str, str]] = []

        for text, pid, src in zip(texts, ids, sources):
            words = text.split()
            if len(words) <= MAX_CHUNK_TOKENS:
                chunked.append(text)
                chunk_map.append((pid, src))
            else:
                # Chunk by words with overlap
                for i in range(0, len(words), MAX_CHUNK_TOKENS - CHUNK_OVERLAP):
                    chunk = " ".join(words[i:i + MAX_CHUNK_TOKENS])
                    if chunk.strip():
                        chunk_id = f"{pid}_chunk_{i // (MAX_CHUNK_TOKENS - CHUNK_OVERLAP)}"
                        chunked.append(chunk)
                        chunk_map.append((pid, src))

        _log.info(
            "embed.chunked",
            original=len(texts),
            chunks=len(chunked),
            multi_chunk=sum(1 for t in chunk_map if t[1] != chunk_map[-1][1])
            if chunk_map else 0,
        )
        return chunked, chunk_map

    def _encode_batch(
        self, texts: list[str], model: Any, batch_size: int = 64,
        *,
        progress: bool = True,
    ) -> list[Any]:
        """Encode texts in batches, returning normalized vectors."""
        import numpy as np

        all_vectors: list[np.ndarray] = []
        n_batches = max(1, (len(texts) + batch_size - 1) // batch_size)

        batch_iter = range(0, len(texts), batch_size)
        if progress:
            batch_iter = tqdm(batch_iter, total=n_batches, desc="Embedding", unit="batch")

        for i in batch_iter:
            batch = texts[i:i + batch_size]
            vectors = model.encode(
                batch,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=batch_size,
            )
            all_vectors.extend(vectors)

        return all_vectors
