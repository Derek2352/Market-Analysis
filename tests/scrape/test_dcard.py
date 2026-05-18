"""dcard parser tests — offline against saved HTML fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.scrape.dcard import _parse_dcard_post
import json

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "dcard"


class TestSearch:

    def test_parses_search_json(self):
        data = json.loads((FIXTURES / "search.json").read_text(encoding="utf-8"))
        assert len(data) == 2
        post = _parse_dcard_post(data[0])
        assert post is not None
        assert post.source == "dcard"
        assert post.region == "TW"
        assert "iPhone" in post.title
        assert post.engagement_metrics["likes"] == 234

    def test_handles_empty(self):
        post = _parse_dcard_post({})
        assert post.body == ""


class TestParse:

    pass

