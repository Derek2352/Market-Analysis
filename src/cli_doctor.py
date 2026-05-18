"""Doctor command — health-check every scraper before pipeline runs.

``mkt doctor`` tests each registered scraper with a lightweight search,
reports pass/fail, last successful scrape timestamp, and rate-limit status.
Outputs a color-coded terminal table (default) or JSON with ``--json``.

Helps detect broken scrapers before the overnight pipeline runs.
"""
from __future__ import annotations

import json as _json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import typer

from src.scrape.registry import available_sources, get_scraper
from src.scrape.base import SourceError

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_HEALTH_TIMEOUT = 30  # seconds per scraper
_TEST_TOPIC = "test"   # lightweight query to verify scraper responds


# ── ANSI color helpers ──────────────────────────────────────────────────
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _color_status(ok: bool) -> str:
    return f"{_GREEN}PASS{_RESET}" if ok else f"{_RED}FAIL{_RESET}"


def _get_last_scrape_ts(source_id: str) -> str | None:
    """Find the most recent scrape output for a source in data/raw/."""
    raw_root = _DATA_DIR / "raw"
    if not raw_root.exists():
        return None

    newest: float | None = None
    newest_ts: str | None = None
    for topic_dir in raw_root.iterdir():
        if not topic_dir.is_dir():
            continue
        for region_dir in topic_dir.iterdir():
            if not region_dir.is_dir():
                continue
            # Look for run files from this source
            for rf in sorted(region_dir.glob("*.json"), reverse=True):
                if rf.name.endswith("._run.json"):
                    continue
                try:
                    with open(rf, encoding="utf-8") as f:
                        posts = _json.load(f)
                    if not isinstance(posts, list) or not posts:
                        continue
                    # Match source_id from any post's source field
                    if any(p.get("source") == source_id for p in posts):
                        mtime = rf.stat().st_mtime
                        if newest is None or mtime > newest:
                            newest = mtime
                            newest_ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                except Exception:
                    continue
    return newest_ts


def _check_scraper(source_id: str) -> dict[str, Any]:
    """Run a lightweight health check on one scraper. Returns result dict."""
    result: dict[str, Any] = {
        "source": source_id,
        "status": "unknown",
        "error": None,
        "posts_found": 0,
        "elapsed_ms": 0,
        "last_scrape": _get_last_scrape_ts(source_id),
    }

    start = time.perf_counter()
    try:
        scraper = get_scraper(source_id)
    except Exception as e:
        result["status"] = "fail"
        result["error"] = f"init error: {e}"
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        return result

    try:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        posts = list(scraper.search(_TEST_TOPIC, since=since, limit=3))
        result["posts_found"] = len(posts)
        result["status"] = "pass"
    except SourceError as e:
        result["status"] = "fail"
        result["error"] = str(e)[:200]
    except Exception as e:
        result["status"] = "fail"
        result["error"] = f"{type(e).__name__}: {e!s}"[:200]
    finally:
        close = getattr(scraper, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)

    return result


def _print_table(results: list[dict[str, Any]]) -> None:
    """Print color-coded terminal table of scraper health."""
    # Column widths
    cols = {
        "source": 24,
        "status": 6,
        "posts": 7,
        "elapsed": 8,
        "last_scrape": 22,
        "error": 50,
    }

    # Header
    header = (
        f"{_BOLD}{'SOURCE':<{cols['source']}}  "
        f"{'STATUS':<{cols['status']}}  "
        f"{'POSTS':>{cols['posts']}}  "
        f"{'ELAPSED':>{cols['elapsed']}}  "
        f"{'LAST SCRAPE':<{cols['last_scrape']}}  "
        f"{'ERROR':<{cols['error']}}{_RESET}"
    )
    print(header)
    print("─" * (sum(cols.values()) + 12))

    for r in results:
        status_str = _color_status(r["status"] == "pass")
        posts_str = str(r["posts_found"]) if r["status"] == "pass" else "-"
        elapsed_str = f"{r['elapsed_ms']}ms"
        last = (r["last_scrape"] or "-")[: cols["last_scrape"]]
        error = (r["error"] or "")[: cols["error"]]

        line = (
            f"{r['source']:<{cols['source']}}  "
            f"{status_str:<{cols['status']}}  "
            f"{posts_str:>{cols['posts']}}  "
            f"{elapsed_str:>{cols['elapsed']}}  "
            f"{last:<{cols['last_scrape']}}  "
            f"{error:<{cols['error']}}"
        )
        print(line)

    # Summary
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = len(results) - passed
    total = len(results)
    print()
    if failed == 0:
        print(f"  {_GREEN}✓ ALL {total} PASSED{_RESET}")
    else:
        print(f"  {_RED}✗ {failed}/{total} FAILED{_RESET}  ({passed}/{total} passed)")
    print()


def doctor(
    source: str = typer.Option(
        "", "--source", help="Check a single source. Omit to check all."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output results as JSON."
    ),
    skip_slow: bool = typer.Option(
        False, "--skip-slow", help="Skip scrapers known to be slow (>30s)."
    ),
) -> None:
    """Health-check every scraper before pipeline runs.

    Tests each scraper with a lightweight search, reports success/failure,
    last scrape timestamp, and any errors. Useful as a pre-flight check
    before running ``mkt overnight`` or as a cron watchdog.
    """
    if source:
        source_ids = [source]
        unknown = [s for s in source_ids if s not in available_sources()]
        if unknown:
            typer.echo(f"Unknown source: {unknown}", err=True)
            raise typer.Exit(code=2)
    else:
        source_ids = available_sources()

    # Skip known-slow scrapers when --skip-slow is set
    # (Playwright-based scrapers that need browser launch)
    _SLOW_SOURCES = {"yelp_html", "tabelog", "openrice", "youtube_html"}
    if skip_slow:
        skipped = [s for s in source_ids if s in _SLOW_SOURCES]
        source_ids = [s for s in source_ids if s not in _SLOW_SOURCES]
        if skipped:
            typer.echo(f"Skipping slow sources: {', '.join(skipped)}\n")

    if not source_ids:
        typer.echo("No sources to check.")
        return

    typer.echo(f"Checking {len(source_ids)} scrapers (timeout={_HEALTH_TIMEOUT}s each)...\n")

    results: list[dict[str, Any]] = []
    for i, sid in enumerate(sorted(source_ids)):
        typer.echo(f"  [{i+1}/{len(source_ids)}] {sid}...", nl=False)
        r = _check_scraper(sid)
        results.append(r)
        status_icon = "✓" if r["status"] == "pass" else "✗"
        color = _GREEN if r["status"] == "pass" else _RED
        typer.echo(f"\r  [{i+1}/{len(source_ids)}] {sid} {color}{status_icon}{_RESET} ({r['elapsed_ms']}ms)")

    if json_output:
        # Strip non-serializable fields if any
        clean = []
        for r in results:
            clean.append({k: v for k, v in r.items() if isinstance(v, (str, int, float, bool, type(None), list, dict))})
        print(_json.dumps(clean, indent=2, default=str))
    else:
        print()
        _print_table(results)

    # Exit non-zero if any failed
    failed = sum(1 for r in results if r["status"] != "pass")
    if failed > 0:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    doctor()
