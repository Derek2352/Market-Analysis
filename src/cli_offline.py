"""``mkt synthesize-offline`` — persona synthesis without an LLM API key.

Exports the synthesizer's exact prompts, validates hand-written (or
assistant-written) answers with the synthesizer's own grounding checks, and
imports them through the real persona/journey builders. See
``src/pipeline/offline_synthesis.py`` for the workflow.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from src.pipeline import offline_synthesis as O

_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "data"

offline_app = typer.Typer(
    no_args_is_help=True,
    help="Synthesize personas without an API key: export prompts, write answers, import.",
)


def _groups(clusters: str | None, merge: list[str]) -> list[list[str]]:
    groups = [[c.strip()] for c in (clusters or "").split(",") if c.strip()]
    groups += [[p.strip() for p in m.split("+") if p.strip()] for m in merge]
    return groups


@offline_app.command("export")
def export_cmd(
    topic: Annotated[str, typer.Option(..., "--topic")],
    region: Annotated[str, typer.Option(..., "--region")],
    out: Annotated[Path, typer.Option(..., "--out", help="Directory for the prompt bundle.")],
    clusters: Annotated[str | None, typer.Option("--clusters", help="Comma-separated cluster ids, one persona each.")] = None,
    merge: Annotated[list[str], typer.Option("--merge", help="Clusters to merge into one persona, e.g. cluster_001+cluster_002. Repeatable.")] = [],  # noqa: B006
    run_id: Annotated[str | None, typer.Option("--run-id", help="Clustering run id (default: latest).")] = None,
) -> None:
    """Write rules / evidence / persona task for each target."""
    try:
        inputs = O.load_inputs(_DATA_DIR, topic, region, run_id)
        targets = O.export(out, inputs, topic, region, _groups(clusters, merge))
    except O.OfflineSynthesisError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    for name, c in targets.items():
        typer.echo(f"  {name}: {c.size} posts -> {out / name}")
    typer.echo(
        f"\nNext: for each target, answer persona_task.txt (given rules.txt + evidence.txt) "
        f"in {O.PERSONA_RESPONSE}, then run `mkt synthesize-offline validate {out}`."
    )


@offline_app.command("validate")
def validate_cmd(out: Annotated[Path, typer.Argument(help="Bundle directory from export.")]) -> None:
    """Check answers with the synthesizer's grounding validators."""
    try:
        statuses = O.validate(out, _DATA_DIR)
    except O.OfflineSynthesisError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    bad = False
    for st in statuses:
        for kind, errs, nxt in (
            ("persona", st.persona_errors, O.PERSONA_RESPONSE),
            ("journey", st.journey_errors, O.JOURNEY_RESPONSE),
        ):
            if errs is None:
                if kind == "journey" and st.persona_errors != []:
                    continue  # journey task isn't available until the persona passes
                typer.echo(f"  {st.name} {kind}: waiting for {nxt}")
                bad = True
            elif errs:
                bad = True
                typer.echo(f"  {st.name} {kind}: INVALID ({len(errs)})")
                for e in errs[:20]:
                    typer.echo(f"      - {e}")
            else:
                extra = " (journey_task.txt written)" if kind == "persona" else ""
                typer.echo(f"  {st.name} {kind}: VALID{extra}")
    if bad:
        raise typer.Exit(code=1)
    typer.echo(f"\nAll targets valid. Next: mkt synthesize-offline import {out}")


@offline_app.command("import")
def import_cmd(out: Annotated[Path, typer.Argument(help="Bundle directory from export.")]) -> None:
    """Build and save personas + journeys from validated answers."""
    try:
        written = O.import_(out, _DATA_DIR)
    except O.OfflineSynthesisError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    for persona, journey in written:
        typer.echo(f"  {persona.name}  persona={persona.id}  journey={journey.id}  confidence={persona.confidence}")
    if written:
        typer.echo(f"\nRender: mkt render run {written[0][0].run_id}")
