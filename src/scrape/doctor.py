"""Scrape-doctor — run every parser against stored HTML fixtures.

Invoked via ``mkt scrape-doctor``.  For each registered source that has HTML
fixtures, loads the saved snapshot, runs the scraper's parse logic against it,
and reports success or drift.

A failing fixture is a signal the source may have changed its markup — the
scraper likely needs a parser update.  Fixtures are saved by scrapers during
normal runs (Openrice, Reddit old-reddit, etc.) via ``FixtureStore.save()``.

Sources that use public JSON (LIHKG, App Store HK) are skipped — their
"fixtures" are JSON response dumps, not HTML.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import typer

from src.scrape.base import FixtureStore
from src.scrape.registry import available_sources, get_scraper
from src.regions.registry import get_region

app = typer.Typer(no_args_is_help=False, add_completion=False, invoke_without_command=True)
_log = structlog.get_logger(__name__)

# Sources whose scrapers use public JSON (not HTML parsers).
# These are skipped by scrape-doctor — there's no HTML to check.
_JSON_SOURCES = {"lihkg", "app_store_hk", "app_store_tw", "app_store_us", "app_store_jp"}

# Test fixture directories (checked when no runtime fixtures exist).
_TEST_FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "html"


@app.callback()
def doctor(
    source: str = typer.Option(
        "", "--source", help="Check a single source. Omit to check all."
    ),
    region: str = typer.Option(
        "", "--region", help="Filter sources to a specific region (e.g., HK, TW, US, JP)."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show fixture names and parse details."
    ),
) -> None:
    """Run registered parsers against stored HTML fixtures.

    A passing test means the scraper's parser can still parse its reference
    HTML.  A failure means the parser may have drifted from the live site's
    current markup — run ``mkt scrape`` to regenerate fixtures.
    """
    if source:
        source_ids = [source]
        unknown = [s for s in source_ids if s not in available_sources()]
        if unknown:
            typer.echo(f"Unknown source: {unknown}", err=True)
            raise typer.Exit(code=2)
    elif region:
        # Filter to a specific region's sources
        region_cfg = get_region(region)
        all_ids = {s.source_id for s in region_cfg.sources}
        source_ids = [s for s in available_sources() if s in all_ids and s not in _JSON_SOURCES]
        typer.echo(f"Region: {region_cfg.display_name} ({len(source_ids)} sources)")
    else:
        source_ids = [s for s in available_sources() if s not in _JSON_SOURCES]

    if not source_ids:
        typer.echo("No HTML-based sources registered. Nothing to check.")
        return

    results: dict[str, dict[str, Any]] = {}

    for source_id in sorted(source_ids):
        fixtures = FixtureStore(source_id)
        names = fixtures.list_fixtures()

        if not names:
            typer.echo(f"  {source_id}: no fixtures — run a scrape first")
            continue

        typer.echo(f"\n  {source_id} ({len(names)} fixtures):")

        passed = 0
        failed = 0
        for name in names:
            try:
                html, meta = fixtures.load(name)
            except Exception as e:
                typer.echo(f"    ✗ {name} — load error: {e}")
                failed += 1
                continue

            ok, detail = _check_parser(source_id, html, meta, fixture_name=name)
            if ok:
                if verbose:
                    typer.echo(f"    ✓ {name} — {detail}")
                passed += 1
            else:
                typer.echo(f"    ✗ {name} — {detail}")
                failed += 1

        results[source_id] = {
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
        }

    # Summary
    typer.echo("\n  ── Summary ──")
    total_pass = sum(r["passed"] for r in results.values())
    total_fail = sum(r["failed"] for r in results.values())
    total = total_pass + total_fail

    if total == 0:
        typer.echo("  No fixtures found. Run scrapers to generate fixtures.")
        return

    status = "✓ ALL PASS" if total_fail == 0 else f"✗ {total_fail}/{total} FAILED"
    typer.echo(f"  {status}  ({total_pass}/{total} passed)")

    if total_fail > 0:
        typer.echo(
            "\n  A failed fixture likely means the source changed its HTML "
            "structure.\n  Regenerate fixtures with a live scrape, then update "
            "the parser."
        )


def _check_parser(
    source_id: str, html: str, meta: dict, *, fixture_name: str = "",
) -> tuple[bool, str]:
    """Check if a scraper's parser can handle the fixture HTML.

    Strategy:
    1. Look for ``doctor_check`` on the scraper module — a per-source hook
       that invokes the real parser. This is the authoritative check.
    2. If absent, fall back to a generic "HTML is non-empty" sanity check.

    The old per-source structural dict (``_STRUCTURAL_CHECKS``) is gone
    because the assertions were too crude (``has-links`` on detail pages,
    ``has-json-structure`` on XSS-prefixed JSON) and produced false alarms.
    """
    if not html or not html.strip():
        return False, "empty HTML / payload"

    hook = _load_doctor_hook(source_id)
    if hook is not None:
        try:
            return hook(fixture_name, html, meta or {})
        except Exception as e:  # noqa: BLE001 — parser drift is exactly what we want to catch
            return False, f"{source_id}.doctor_check raised: {e}"

    # Fallback: minimal sanity check for sources without a doctor_check.
    return True, f"{len(html)} bytes (no per-source check defined)"


def _load_doctor_hook(source_id: str):
    """Import the scraper module and return its ``doctor_check`` if present."""
    import importlib
    try:
        mod = importlib.import_module(f"src.scrape.{source_id}")
    except ModuleNotFoundError:
        return None
    return getattr(mod, "doctor_check", None)


def _has_test_fixtures(source_id: str) -> list[str]:
    """Return list of test fixture names for *source_id*, or empty list."""
    d = _TEST_FIXTURES_DIR / source_id
    if not d.is_dir():
        return []
    return sorted(
        f.name for f in d.iterdir()
        if f.suffix in (".html", ".json") and not f.name.endswith(".meta.json")
    )


# Per-source structural checks were removed in Phase 8. The doctor now
# calls each scraper module's ``doctor_check`` function — a real parser
# invocation against the fixture. See e.g. ``src/scrape/discuss_hk.py:doctor_check``.
# Modules without a hook fall through to the "non-empty HTML" sanity check.


if __name__ == "__main__":
    app()
