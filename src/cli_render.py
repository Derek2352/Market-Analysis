"""``mkt render persona|journey|run`` — Phase 8 render CLI.

Looks up the requested Persona / JourneyMap JSON from the data/ tree the
synthesize stage wrote, then drives the Phase 8 renderer. The lookup is
disambiguated by id alone (sha-prefixed), so callers don't have to
remember which topic/region directory holds the file.
"""
from __future__ import annotations

import json as _json
import time
from pathlib import Path
from typing import Annotated, Optional

import typer

from src.render.bundle import render_run
from src.render.journey_map import render_journey_map
from src.render.persona_card import render_persona_card
from src.schemas.synthesis import JourneyMap, Persona

_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "data"
_PERSONAS_ROOT = _DATA_DIR / "personas"
_JOURNEYS_ROOT = _DATA_DIR / "journeys"
_RUNS_ROOT     = _DATA_DIR / "runs"
_RENDERS_ROOT  = _DATA_DIR / "renders"

render_app = typer.Typer(no_args_is_help=True, help="Render personas + journeys to PNG.")


# ---------------------------------------------------------------------------
# JSON discovery
# ---------------------------------------------------------------------------


def _find_persona(persona_id: str) -> tuple[Persona, Path] | None:
    if not _PERSONAS_ROOT.exists():
        return None
    for f in sorted(_PERSONAS_ROOT.rglob("persona_*.json")):
        try:
            payload = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("id") == persona_id:
            try:
                return Persona(**payload), f
            except Exception:
                continue
    return None


def _find_journey_for_persona(persona_id: str) -> tuple[JourneyMap, Path] | None:
    if not _JOURNEYS_ROOT.exists():
        return None
    for f in sorted(_JOURNEYS_ROOT.rglob("journey_*.json")):
        try:
            payload = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("persona_id") == persona_id:
            try:
                return JourneyMap(**payload), f
            except Exception:
                continue
    return None


def _list_available_persona_ids(limit: int = 20) -> list[str]:
    out: list[str] = []
    if not _PERSONAS_ROOT.exists():
        return out
    for f in sorted(_PERSONAS_ROOT.rglob("persona_*.json")):
        try:
            payload = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = payload.get("id")
        if pid:
            out.append(pid)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@render_app.command("persona")
def render_persona_cmd(
    persona_id: Annotated[str, typer.Argument(help="Persona id, e.g. persona_a4f9c7e2")],
    out: Annotated[
        Optional[Path],
        typer.Option("--out", help="Output PNG path (default data/renders/<id>.png)."),
    ] = None,
) -> None:
    """Render one persona card to PNG."""
    found = _find_persona(persona_id)
    if found is None:
        ids = _list_available_persona_ids()
        msg = (
            f"persona {persona_id!r} not found under {_PERSONAS_ROOT}.\n"
            f"Available ids (showing up to 20):\n  "
            + ("\n  ".join(ids) if ids else "<none — run mkt synthesize first>")
        )
        typer.echo(msg, err=True)
        raise typer.Exit(code=1)
    persona, _src = found
    out_path = out or (_RENDERS_ROOT / f"{persona_id}.png")
    t0 = time.perf_counter()
    rendered = render_persona_card(persona, out_path)
    dt = time.perf_counter() - t0
    size_kb = rendered.stat().st_size / 1024
    typer.echo(f"persona  → {rendered}  ({size_kb:.0f} KB, {dt:.2f}s)")


@render_app.command("journey")
def render_journey_cmd(
    persona_id: Annotated[str, typer.Argument(help="Persona id whose journey to render")],
    out: Annotated[
        Optional[Path],
        typer.Option("--out", help="Output PNG path (default data/renders/<journey_id>.png)."),
    ] = None,
    topic: Annotated[
        str, typer.Option("--topic", help="Topic label for the header"),
    ] = "",
) -> None:
    """Render one journey map to PNG (looked up by its persona id)."""
    persona_found = _find_persona(persona_id)
    if persona_found is None:
        ids = _list_available_persona_ids()
        typer.echo(
            f"persona {persona_id!r} not found. Available ids:\n  "
            + ("\n  ".join(ids) if ids else "<none>"),
            err=True,
        )
        raise typer.Exit(code=1)
    persona, _ = persona_found

    journey_found = _find_journey_for_persona(persona_id)
    if journey_found is None:
        typer.echo(
            f"No journey found for persona {persona_id!r}.",
            err=True,
        )
        raise typer.Exit(code=1)
    journey, _ = journey_found

    out_path = out or (_RENDERS_ROOT / f"{journey.id}.png")
    t0 = time.perf_counter()
    rendered = render_journey_map(journey, persona, out_path, topic=topic)
    dt = time.perf_counter() - t0
    size_kb = rendered.stat().st_size / 1024
    typer.echo(f"journey  → {rendered}  ({size_kb:.0f} KB, {dt:.2f}s)")


@render_app.command("run")
def render_run_cmd(
    run_id: Annotated[str, typer.Argument(help="Run id, e.g. 20260519T080000Z")],
    out_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--out-dir",
            help="Bundle directory (default data/renders/<run_id>/).",
        ),
    ] = None,
    zip_bundle: Annotated[
        bool,
        typer.Option("--zip/--no-zip", help="Also produce a <run_id>.zip alongside the bundle."),
    ] = False,
) -> None:
    """Render every persona + journey for a run, plus an index.html."""
    out_dir = out_dir or (_RENDERS_ROOT / run_id)
    t0 = time.perf_counter()
    try:
        result = render_run(
            run_id,
            out_dir,
            personas_root=_PERSONAS_ROOT,
            journeys_root=_JOURNEYS_ROOT,
            runs_root=_RUNS_ROOT,
            zip_bundle=zip_bundle,
        )
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    dt = time.perf_counter() - t0
    typer.echo(
        f"run {run_id} → {result.out_dir}  "
        f"({len(result.persona_pngs)} personas, "
        f"{len(result.journey_pngs)} journeys, "
        f"{dt:.1f}s total)"
    )
    typer.echo(f"  index: {result.index_html}")
    if result.zip_path:
        typer.echo(f"  zip:   {result.zip_path}")
