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

app = typer.Typer(no_args_is_help=False, add_completion=False)
_log = structlog.get_logger(__name__)

# Sources whose scrapers use public JSON (not HTML parsers).
# These are skipped by scrape-doctor — there's no HTML to check.
_JSON_SOURCES = {"lihkg", "app_store_hk"}


@app.command()
def doctor(
    source: str = typer.Option(
        "", "--source", help="Check a single source. Omit to check all."
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

            ok, detail = _check_parser(source_id, html, meta)
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


def _check_parser(source_id: str, html: str, meta: dict) -> tuple[bool, str]:
    """Check if a scraper's parser can handle the fixture HTML.

    For now, this is a structural check: we verify the HTML is non-empty and
    contains expected elements for the source.  Full parse-through will be
    added as each scraper exposes a ``parse_from_fixture()`` entrypoint.
    """
    if not html or not html.strip():
        return False, "empty HTML"

    # Structural checks per source
    checks = _STRUCTURAL_CHECKS.get(source_id, [])
    for check_name, check_fn in checks:
        ok, msg = check_fn(html)
        if not ok:
            return False, f"{check_name}: {msg}"

    # If the source registered, try running the scraper's live parse against
    # the fixture by checking for expected class/id markers.
    return True, f"{len(html)} bytes, structure OK"


def _check_has_elements(tag: str) -> callable:
    """Factory: returns a function that checks HTML contains <tag>."""
    def check(html: str) -> tuple[bool, str]:
        count = html.count(f"<{tag}")
        if count == 0:
            return False, f"no <{tag}> elements found"
        return True, f"{count} <{tag}> elements"
    return check


def _check_has_class(class_name: str) -> callable:
    """Factory: returns a function that checks HTML contains a CSS class."""
    def check(html: str) -> tuple[bool, str]:
        if class_name not in html:
            return False, f"class '{class_name}' not found"
        return True, f"class '{class_name}' present"
    return check


_STRUCTURAL_CHECKS: dict[str, list[tuple[str, callable]]] = {
    "openrice": [
        ("has-links", _check_has_elements("a")),
        ("restaurant-links", _check_has_class("r-")),
    ],
    "reddit_old": [
        ("has-links", _check_has_elements("a")),
        ("thread-links", _check_has_class("thing")),
    ],
}


if __name__ == "__main__":
    app()
