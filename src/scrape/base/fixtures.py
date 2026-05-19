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
        """Load a fixture by name.

        Returns ``(body, metadata_dict)``. The body is the raw text of the
        saved fixture file — HTML for HTML scrapers, JSON for JSON-API
        scrapers (Reddit, app stores). Sources whose ``doctor_check`` parses
        the payload decide on the format.

        Raises ``FileNotFoundError`` if neither ``<name>.html`` nor
        ``<name>.json`` exists.
        """
        slug = _slug(name)
        for ext in (".html", ".json"):
            body_path = self._dir / f"{slug}{ext}"
            if body_path.exists():
                break
        else:
            raise FileNotFoundError(
                f"Fixture not found: {self._dir / f'{slug}.{{html,json}}'}",
            )

        body = body_path.read_text(encoding="utf-8")
        meta_path = self._dir / f"{slug}.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        return body, meta

    def list_fixtures(self) -> list[str]:
        """List available fixture names (without extension).

        Globs both ``*.html`` and ``*.json`` so JSON-API scrapers (Reddit,
        app stores) can drop their fixtures alongside the HTML ones without
        a parallel directory tree.
        """
        names: set[str] = set()
        for ext in ("*.html", "*.json"):
            for p in self._dir.glob(ext):
                if p.name.startswith(".") or p.name.endswith(".meta.json"):
                    continue
                names.add(p.stem)
        return sorted(names)

    def fixture_path(self, name: str) -> Path:
        """Absolute path to a fixture's body file (.html if present, else .json)."""
        slug = _slug(name)
        html_path = self._dir / f"{slug}.html"
        if html_path.exists():
            return html_path
        return self._dir / f"{slug}.json"


def _slug(name: str) -> str:
    import re
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "untitled"
