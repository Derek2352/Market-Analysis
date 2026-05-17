from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_posts (
    source         TEXT NOT NULL,
    source_post_id TEXT NOT NULL,
    region         TEXT NOT NULL,
    topic_slug     TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    PRIMARY KEY (source, source_post_id)
);
"""


class DedupIndex:
    """SQLite-backed dedup index keyed on (source, source_post_id).

    Stores no content — just enough to answer "have we ever seen this post?"
    The PK is global across topics, so a review surfaced under topic A then
    re-encountered under topic B is treated as a duplicate (correct: we
    already wrote it once, and it has a stable canonical URL).
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def is_seen(self, source: str, source_post_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM seen_posts WHERE source=? AND source_post_id=?",
            (source, source_post_id),
        )
        return cur.fetchone() is not None

    def mark_seen(
        self,
        *,
        source: str,
        source_post_id: str,
        region: str,
        topic_slug: str,
    ) -> bool:
        """Insert a row. Returns True if newly inserted, False if it already existed."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                "INSERT INTO seen_posts "
                "(source, source_post_id, region, topic_slug, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source, source_post_id, region, topic_slug, now, now),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            self._conn.execute(
                "UPDATE seen_posts SET last_seen_at=? "
                "WHERE source=? AND source_post_id=?",
                (now, source, source_post_id),
            )
            self._conn.commit()
            return False

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DedupIndex":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
