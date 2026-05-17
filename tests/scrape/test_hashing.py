from __future__ import annotations

import pytest

from src.scrape.utils.hashing import hash_author


def test_hash_is_stable_for_same_input() -> None:
    assert hash_author("alice", salt="s") == hash_author("alice", salt="s")


def test_hash_changes_with_salt() -> None:
    a = hash_author("alice", salt="salt-1")
    b = hash_author("alice", salt="salt-2")
    assert a != b


def test_empty_author_returns_empty_string() -> None:
    assert hash_author("", salt="s") == ""


def test_missing_salt_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTHOR_HASH_SALT", raising=False)
    with pytest.raises(RuntimeError, match="AUTHOR_HASH_SALT"):
        hash_author("alice")


def test_hash_is_64_hex_chars() -> None:
    h = hash_author("alice", salt="s")
    assert len(h) == 64
    int(h, 16)  # raises if not hex
