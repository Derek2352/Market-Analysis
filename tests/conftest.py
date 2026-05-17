from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _author_hash_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure a deterministic salt is set for every test."""
    monkeypatch.setenv("AUTHOR_HASH_SALT", "test-salt-not-secret")


@pytest.fixture
def fixtures_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "fixtures")
