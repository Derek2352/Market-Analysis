"""HTML fixture store for parser tests and scrape-doctor.

Each supported source keeps one or more saved HTML snapshots in
``tests/fixtures/html/{source_id}/``.  The scrape-doctor command
(``mkt scrape-doctor``) loads these fixtures through each scraper's parser to
detect markup drift — a signal that a site may have changed its layout.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "html"


class FixtureStore:
    """Read and write HTML fixtures for a given source.

    Parameters
    ----------
    source_id:
        Source identifier (e.g. ``"openrice"``).
    fixtures_dir:
        Root directory for HTML fixtures.  Defaults to the project's
        ``tests/fixtures/html/``.
    """

    def __init__(self, source_id: str, fixtures_dir: Path | None = None) -> None:
        self.source_id = source_id
        self._dir = (fixtures_dir or FIXTURES_DIR) / source_id
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Writing (used during development to capture reference HTML)
    # ------------------------------------------------------------------

    def save(self, name: str, html: str, metadata: dict[str, Any] | None = None) -> Path:
        """Save an HTML snapshot.

        Parameters
        ----------
        name:
            Short descriptive name (e.g. ``"search_lihkg_prep"``).  Will be
            slugified into a filename.
        html:
            Raw HTML content.
        metadata:
            Arbitrary dict stored alongside the HTML as JSON (URL, timestamp,
            request params).
        """
        slug = _slug(name)
        html_path = self._dir / f"{slug}.html"
        meta_path = self._dir / f"{slug}.meta.json"

        html_path.write_text(html, encoding="utf-8")

        meta: dict[str, Any] = {
            "source_id": self.source_id,
            "fixture_name": name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            meta.update(metadata)
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        return html_path

    # ------------------------------------------------------------------
    # Reading (used by tests and scrape-doctor)
    # ------------------------------------------------------------------

    def load(self, name: str) -> tuple[str, dict[str, Any]]:
        """Load an HTML fixture by name.

        Returns ``(html, metadata_dict)``.

        Raises ``FileNotFoundError`` if the fixture doesn't exist.
        """
        slug = _slug(name)
        html_path = self._dir / f"{slug}.html"
        meta_path = self._dir / f"{slug}.meta.json"

        if not html_path.exists():
            raise FileNotFoundError(f"Fixture not found: {html_path}")

        html = html_path.read_text(encoding="utf-8")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {}
        return html, meta

    def list_fixtures(self) -> list[str]:
        """List available fixture names (without extension)."""
        return sorted(
            p.stem for p in self._dir.glob("*.html") if not p.name.startswith(".")
        )

    def fixture_path(self, name: str) -> Path:
        """Absolute path to a fixture's HTML file."""
        return self._dir / f"{_slug(name)}.html"


def _slug(name: str) -> str:
    import re
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "untitled"
