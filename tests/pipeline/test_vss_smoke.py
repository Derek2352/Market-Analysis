"""DuckDB VSS smoke tests.

The "VSS fix" commits in this branch's history are a signal the VSS
extension setup was fragile. These tests assert the load path used by
``src.pipeline.embed.EmbeddingStore`` still works end-to-end on a fresh
DB: install + load + HNSW index + cosine-similarity query returns the
expected nearest neighbour.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

duckdb = pytest.importorskip("duckdb")


def _probe_vss() -> str | None:
    """Try to install + load the VSS extension on a throwaway in-memory DB.

    Returns None on success, an error message on failure. We skip the whole
    module on failure with that message so an unreachable extension CDN
    surfaces a clear reason rather than a stack trace per test.
    """
    try:
        con = duckdb.connect(":memory:")
        try:
            con.execute("INSTALL vss;")
            con.execute("LOAD vss;")
        finally:
            con.close()
        return None
    except Exception as e:
        return f"VSS extension unavailable in this environment: {e}"


_skip_reason = _probe_vss()
pytestmark = pytest.mark.skipif(
    _skip_reason is not None,
    reason=_skip_reason or "vss probe ok",
)


def _open_with_vss(db_path: Path):
    """Open a DB with VSS loaded — same sequence EmbeddingStore uses."""
    con = duckdb.connect(str(db_path))
    con.execute("INSTALL vss;")
    con.execute("LOAD vss;")
    return con


def test_vss_extension_loads_and_lists_array_distance(tmp_path: Path) -> None:
    """VSS install + load must succeed and expose `array_cosine_distance`."""
    con = _open_with_vss(tmp_path / "smoke.duckdb")
    try:
        # Functions provided by the VSS extension. If LOAD vss silently
        # failed, these would not be callable.
        row = con.execute(
            "SELECT array_cosine_distance([1.0, 0.0, 0.0]::FLOAT[3], "
            "                              [0.0, 1.0, 0.0]::FLOAT[3])"
        ).fetchone()
        assert row is not None
        # Cosine distance between orthogonal unit vectors is 1.0.
        assert abs(row[0] - 1.0) < 1e-6
    finally:
        con.close()


def test_hnsw_index_round_trip_returns_nearest(tmp_path: Path) -> None:
    """Insert vectors, build the HNSW index, query for nearest neighbour."""
    con = _open_with_vss(tmp_path / "hnsw.duckdb")
    try:
        # Same persistence flag EmbeddingStore sets — without it, HNSW on
        # a file-backed DB raises.
        con.execute("SET hnsw_enable_experimental_persistence = true")

        con.execute(
            "CREATE TABLE vectors (id VARCHAR PRIMARY KEY, vec FLOAT[3])"
        )
        con.executemany(
            "INSERT INTO vectors VALUES (?, ?)",
            [
                ("a", [1.0, 0.0, 0.0]),
                ("b", [0.0, 1.0, 0.0]),
                ("c", [0.9, 0.1, 0.0]),  # closest to 'a' but distinct
                ("d", [0.0, 0.0, 1.0]),
            ],
        )

        con.execute("CREATE INDEX idx ON vectors USING hnsw (vec)")

        # Query: nearest to [1, 0, 0].
        results = con.execute(
            "SELECT id, array_cosine_distance(vec, [1.0, 0.0, 0.0]::FLOAT[3]) AS d "
            "FROM vectors ORDER BY d LIMIT 2"
        ).fetchall()

        ids = [r[0] for r in results]
        assert ids == ["a", "c"], f"unexpected nearest neighbours: {results}"
        # Cosine distance to itself is 0.
        assert results[0][1] == 0.0
    finally:
        con.close()


def test_vss_index_persists_across_reopen(tmp_path: Path) -> None:
    """The HNSW index must survive close+reopen on a file-backed DB.

    EmbeddingStore depends on this — otherwise re-running ``mkt cluster``
    after ``mkt embed`` would silently rebuild every time.
    """
    db = tmp_path / "persist.duckdb"

    con = _open_with_vss(db)
    try:
        con.execute("SET hnsw_enable_experimental_persistence = true")
        con.execute("CREATE TABLE v (id VARCHAR PRIMARY KEY, vec FLOAT[3])")
        con.execute("INSERT INTO v VALUES ('a', [1.0, 0.0, 0.0])")
        con.execute("CREATE INDEX idx ON v USING hnsw (vec)")
    finally:
        con.close()

    con = _open_with_vss(db)
    try:
        con.execute("SET hnsw_enable_experimental_persistence = true")
        # If the index was lost, this would silently fall back to scan,
        # but the index name must still be visible in duckdb_indexes().
        idx_rows = con.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE table_name = 'v'"
        ).fetchall()
        idx_names = {r[0] for r in idx_rows}
        assert "idx" in idx_names

        row = con.execute("SELECT vec FROM v WHERE id = 'a'").fetchone()
        assert row is not None
        assert np.allclose(np.array(row[0]), [1.0, 0.0, 0.0])
    finally:
        con.close()
