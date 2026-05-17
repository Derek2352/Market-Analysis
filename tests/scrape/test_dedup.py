from __future__ import annotations

from pathlib import Path

from src.scrape.utils.dedup import DedupIndex


def test_first_insert_returns_true(tmp_path: Path) -> None:
    with DedupIndex(tmp_path / "d.sqlite") as idx:
        assert idx.mark_seen(
            source="s", source_post_id="1", region="HK", topic_slug="t"
        ) is True


def test_second_insert_returns_false(tmp_path: Path) -> None:
    with DedupIndex(tmp_path / "d.sqlite") as idx:
        idx.mark_seen(source="s", source_post_id="1", region="HK", topic_slug="t")
        assert idx.mark_seen(
            source="s", source_post_id="1", region="HK", topic_slug="t"
        ) is False


def test_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "d.sqlite"
    with DedupIndex(db) as idx:
        idx.mark_seen(source="s", source_post_id="1", region="HK", topic_slug="t")
    with DedupIndex(db) as idx:
        assert idx.is_seen("s", "1") is True
        assert idx.is_seen("s", "2") is False


def test_different_topics_share_key(tmp_path: Path) -> None:
    """Same source+post_id under topic A is dedup'd if seen first under topic B."""
    with DedupIndex(tmp_path / "d.sqlite") as idx:
        assert idx.mark_seen(
            source="s", source_post_id="1", region="HK", topic_slug="topic_a"
        ) is True
        assert idx.mark_seen(
            source="s", source_post_id="1", region="HK", topic_slug="topic_b"
        ) is False
