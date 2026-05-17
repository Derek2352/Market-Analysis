from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost
from src.scrape.utils.writer import RunWriter


def _post(i: int) -> RawPost:
    return RawPost(
        id=f"r-{i}",
        source="app_store_hk",
        source_category=SourceCategory.REVIEWS,
        region="HK",
        language="zh-HK",
        language_detected="en",
        url="https://example.com/r",
        author_hash="a" * 64,
        title=f"t {i}",
        body=f"body {i}",
        posted_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        signal_type=SignalType.EXPERIENCE,
    )


def test_writes_records_and_sidecar(tmp_path: Path) -> None:
    w = RunWriter(
        data_dir=tmp_path,
        topic_slug="whatsapp",
        region="HK",
        source="app_store_hk",
        run_id="r1",
    )
    w.add(_post(1))
    w.add(_post(2))
    out = w.finalize(cap_hit=True, cap_hit_apps=["310633997"], duplicates_skipped=3)

    assert out.exists()
    records = json.loads(out.read_text())
    assert len(records) == 2
    assert records[0]["id"] == "r-1"
    assert records[0]["language_detected"] == "en"

    sidecar = tmp_path / "raw/whatsapp/HK/app_store_hk_r1._run.json"
    assert sidecar.exists()
    meta = json.loads(sidecar.read_text())
    assert meta["topic_slug"] == "whatsapp"
    assert meta["records_emitted"] == 2
    assert meta["cap_hit"] is True
    assert meta["cap_hit_apps"] == ["310633997"]
    assert meta["duplicates_skipped"] == 3


def test_atomic_no_partial_file_on_finalize(tmp_path: Path) -> None:
    w = RunWriter(
        data_dir=tmp_path,
        topic_slug="t",
        region="HK",
        source="app_store_hk",
        run_id="r2",
    )
    w.add(_post(1))
    w.finalize()
    siblings = list((tmp_path / "raw/t/HK").iterdir())
    # only the two final files, no .tmp left behind
    assert all(not p.name.endswith(".tmp") for p in siblings)
