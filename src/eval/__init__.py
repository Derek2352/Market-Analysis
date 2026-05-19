"""Eval set for personas + journey grounding.

``src/eval/runner.py`` exposes :func:`run_eval`, which scores a single
fixture against a synthesized run, and :func:`run_eval_suite`, which
walks ``eval/products/`` and aggregates the results.

The eval fixtures live in ``eval/products/`` as JSON files; each one
freezes a small (10-15 post, 2-3 cluster) synthetic dataset plus a
hand-curated list of ground-truth pain points. ``make eval`` runs the
suite against the real LLM; tests use the bundled mock-response
fixtures so CI doesn't need an API key.
"""
from src.eval.runner import (
    EvalReport,
    EvalScore,
    load_fixture,
    run_eval,
    run_eval_suite,
)

__all__ = [
    "EvalReport",
    "EvalScore",
    "load_fixture",
    "run_eval",
    "run_eval_suite",
]
