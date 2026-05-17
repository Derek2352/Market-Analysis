from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import typer

from src.regions.registry import get_region
from src.scrape.registry import available_sources, get_scraper
from src.scrape.utils.dedup import DedupIndex
from src.scrape.utils.logging import configure_logging
from src.scrape.utils.since import parse_since
from src.scrape.utils.writer import RunWriter
from src.scrape.doctor import app as doctor_app

app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(doctor_app, name="scrape-doctor")


@app.callback()
def _main() -> None:
    """Market analytics CLI."""


_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "data"
_LOG_DIR = _ROOT / "logs"


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "untitled"


@app.command()
def scrape(
    topic: Annotated[str, typer.Option(..., "--topic", help="Search term or numeric app id.")],
    region: Annotated[str, typer.Option(..., "--region", help="Canonical region code, e.g. HK.")],
    sources: Annotated[
        str,
        typer.Option(
            "--sources",
            help="Comma-separated source ids. Omit to use the region default list.",
        ),
    ] = "",
    limit: Annotated[
        int, typer.Option("--limit", min=1, help="Max records per source.")
    ] = 500,
    since: Annotated[
        str, typer.Option("--since", help="Relative window, e.g. 90d, 24h, 2w, 6m.")
    ] = "90d",
) -> None:
    """Scrape one or more sources for `topic` in `region`."""
    region_cfg = get_region(region)

    if sources:
        source_ids = [s.strip() for s in sources.split(",") if s.strip()]
    else:
        source_ids = region_cfg.default_source_ids()

    unknown = [s for s in source_ids if s not in available_sources()]
    if unknown:
        typer.echo(
            f"Available sources: {available_sources()}. "
            f"Not implemented: {unknown}. ",
            err=True,
        )
        raise typer.Exit(code=2)

    since_dt = parse_since(since)
    topic_slug = _slugify(topic)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = configure_logging(_LOG_DIR, run_id).bind(
        topic=topic, topic_slug=topic_slug, region=region
    )

    log.info(
        "scrape.start",
        sources=source_ids,
        limit=limit,
        since=since_dt.isoformat(),
    )

    with DedupIndex(_DATA_DIR / "dedup.sqlite") as index:
        for source_id in source_ids:
            writer = RunWriter(
                data_dir=_DATA_DIR,
                topic_slug=topic_slug,
                region=region,
                source=source_id,
                run_id=run_id,
            )
            scraper = get_scraper(source_id)
            emitted = 0
            duplicates = 0
            try:
                for post in scraper.search(topic, since=since_dt, limit=limit):
                    is_new = index.mark_seen(
                        source=source_id,
                        source_post_id=post.id,
                        region=region,
                        topic_slug=topic_slug,
                    )
                    if is_new:
                        writer.add(post)
                        emitted += 1
                    else:
                        duplicates += 1
            finally:
                close = getattr(scraper, "close", None)
                if callable(close):
                    close()

            extra_meta: dict[str, Any] = {"duplicates_skipped": duplicates}
            cap_apps = getattr(scraper, "cap_hit_apps", [])
            extra_meta["cap_hit"] = bool(cap_apps)
            if cap_apps:
                extra_meta["cap_hit_apps"] = list(cap_apps)

            out_path = writer.finalize(**extra_meta)
            log.info(
                "scrape.source.done",
                source=source_id,
                emitted=emitted,
                duplicates=duplicates,
                output=str(out_path.relative_to(_ROOT)),
                cap_hit=extra_meta["cap_hit"],
            )

    log.info("scrape.done")


if __name__ == "__main__":
    app()
