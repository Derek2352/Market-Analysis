from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.schemas.raw import RawPost


class RunWriter:
    """Buffers RawPost records for one (topic, region, source, run) and writes
    them atomically at finalize() time.

    Output layout:
      {data_dir}/raw/{topic_slug}/{region}/{source}_{run_id}.json
      {data_dir}/raw/{topic_slug}/{region}/{source}_{run_id}._run.json
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        topic_slug: str,
        region: str,
        source: str,
        run_id: str,
    ):
        self._dir = data_dir / "raw" / topic_slug / region
        self._dir.mkdir(parents=True, exist_ok=True)
        self._records_path = self._dir / f"{source}_{run_id}.json"
        self._meta_path = self._dir / f"{source}_{run_id}._run.json"
        self._records: list[dict[str, Any]] = []
        self._meta: dict[str, Any] = {
            "topic_slug": topic_slug,
            "region": region,
            "source": source,
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    def add(self, post: RawPost) -> None:
        self._records.append(post.model_dump(mode="json"))

    def finalize(self, **extra_meta: Any) -> Path:
        self._meta["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._meta["records_emitted"] = len(self._records)
        self._meta.update(extra_meta)
        self._atomic_write(self._records_path, self._records)
        self._atomic_write(self._meta_path, self._meta)
        return self._records_path

    @staticmethod
    def _atomic_write(path: Path, payload: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
