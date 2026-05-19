"""``mkt eval`` — run the persona/journey eval set.

Walks ``eval/products/*.json``, runs synthesis against each fixture, and
prints a per-product + overall summary.

The default ``--provider mock`` replays each fixture's canned LLM
responses through an httpx mock transport. It needs no API key and is
the right default for CI and local prompt iteration.

Switch to ``--provider anthropic`` (or ``--provider deepseek``) to drive
the real LLM. This is what ``make eval`` runs after a prompt change to
measure whether recovery / coverage moved.
"""
from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from src.eval.runner import EvalReport, EVAL_DIR, run_eval_suite

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _color_rate(rate: float) -> str:
    if rate >= 0.8:
        c = _GREEN
    elif rate >= 0.5:
        c = _YELLOW
    else:
        c = _RED
    return f"{c}{rate:>5.0%}{_RESET}"


def _color_coverage(score: float) -> str:
    if score >= 3.0:
        c = _GREEN
    elif score >= 2.0:
        c = _YELLOW
    else:
        c = _RED
    return f"{c}{score:.2f}{_RESET}"


def _print_report(report: EvalReport, *, json_out: bool) -> None:
    if json_out:
        typer.echo(_json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
        return
    typer.echo(f"\n{_BOLD}eval suite — {len(report.scores)} fixtures{_RESET}")
    typer.echo(
        f"  {'fixture':<22} {'topic':<14} {'rgn':<4} "
        f"{'recovery':<10} {'coverage':<9} {'personas':<8}"
    )
    typer.echo("  " + "-" * 70)
    for s in report.scores:
        typer.echo(
            f"  {s.name:<22} {s.topic:<14} {s.region:<4} "
            f"{_color_rate(s.recovery_rate):<10}"
            f"({s.recovered_pain_points}/{s.expected_pain_points})  "
            f"{_color_coverage(s.mean_coverage_score):<9}"
            f"  {s.personas_generated:<8}"
        )
        if s.unmatched_themes:
            typer.echo(
                f"    {_DIM}unmatched themes: "
                f"{', '.join(s.unmatched_themes)}{_RESET}"
            )
    typer.echo("  " + "-" * 70)
    typer.echo(
        f"  {_BOLD}mean{_RESET:<22} {'':<14} {'':<4} "
        f"{_color_rate(report.mean_recovery_rate)}      "
        f"{_color_coverage(report.mean_coverage_score)}"
    )


def eval_cmd(
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="LLM backend: mock | anthropic | deepseek. "
                 "Mock replays fixture-canned responses (no API key).",
        ),
    ] = "mock",
    model: Annotated[
        Optional[str],
        typer.Option("--model", help="Override model id."),
    ] = None,
    directory: Annotated[
        Optional[Path],
        typer.Option(
            "--directory",
            help="Path to fixtures dir (defaults to eval/products/).",
        ),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
    min_recovery: Annotated[
        float,
        typer.Option(
            "--min-recovery",
            help="Exit non-zero if mean recovery rate falls below this (0..1).",
            min=0.0, max=1.0,
        ),
    ] = 0.0,
) -> None:
    """Run the persona/journey eval suite.

    Exits non-zero when ``--min-recovery`` is set and the mean recovery
    rate falls below it — wire that into CI to fail on regressions.
    """
    target = Path(directory) if directory else EVAL_DIR
    if not target.exists() or not list(target.glob("*.json")):
        typer.echo(
            f"No eval fixtures at {target}. Add at least one JSON file.",
            err=True,
        )
        raise typer.Exit(code=1)

    api_key = None
    if provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    elif provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY")

    report = run_eval_suite(
        directory=target,
        provider=provider,
        model=model,
        api_key=api_key,
    )
    _print_report(report, json_out=json_out)

    if min_recovery > 0 and report.mean_recovery_rate < min_recovery:
        typer.echo(
            f"\n{_RED}mean recovery {report.mean_recovery_rate:.0%} "
            f"< threshold {min_recovery:.0%}{_RESET}",
            err=True,
        )
        raise typer.Exit(code=2)
