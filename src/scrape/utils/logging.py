from __future__ import annotations

import logging
from pathlib import Path

import structlog


def configure_logging(log_dir: Path, run_id: str) -> structlog.stdlib.BoundLogger:
    """Configure JSON-line logging to {log_dir}/scrape_{run_id}.jsonl + stdout.

    Returns a bound logger pre-tagged with `run_id`. Subsequent `.bind(...)`
    calls add per-source / per-topic context without re-formatting.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"scrape_{run_id}.jsonl"

    handlers: list[logging.Handler] = [
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(),
    ]
    for h in handlers:
        h.setFormatter(logging.Formatter("%(message)s"))

    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger("scrape").bind(run_id=run_id)
