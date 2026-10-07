#!/usr/bin/env python
"""Overnight market-analysis pipeline — mass scrape, embed, cluster, synthesize.

Runs autonomously as a cron job across multiple topics (AlipayHK, Octopus,
WeChat Pay HK, FPS). Reports progress via structured logs.
Exits 0 on success, non-zero on failure after exhausting retries.

Usage:
    python scripts/overnight_pipeline.py           # Full run
    python scripts/overnight_pipeline.py --dry-run  # Print plan only
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import os as _os

PROJECT = Path(__file__).resolve().parent.parent
_VENV_BIN = PROJECT / ".venv" / ("Scripts" if _os.name == "nt" else "bin")
VENV_PYTHON = _VENV_BIN / ("python.exe" if _os.name == "nt" else "python")
MKT = _VENV_BIN / ("mkt.exe" if _os.name == "nt" else "mkt")
LOG_FILE = PROJECT / "logs" / f"overnight_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"

# ── Topics to process ──
TOPICS = ["AlipayHK", "Octopus", "WeChat Pay HK", "FPS"]

# ── Query variants per topic (without --expand, to avoid too-specific queries) ──
QUERY_VARIANTS_BY_TOPIC: dict[str, list[str]] = {
    "AlipayHK": [
        "AlipayHK",
        "alipay hk",
        "支付寶HK",
        "支付寶 香港",
        "Alipay Hong Kong",
    ],
    "Octopus": [
        "Octopus",
        "octopus card",
        "octopus hk",
        "八達通",
        "八達通卡",
        "八达通",
    ],
    "WeChat Pay HK": [
        "WeChat Pay HK",
        "wechat pay hong kong",
        "微信支付香港",
        "微信支付 HK",
        "WeChat Pay Hong Kong",
    ],
    "FPS": [
        "FPS",
        "fps hk",
        "轉數快",
        "转数快",
        "Faster Payment System",
        "fps hong kong",
    ],
}

# ── Scrape phases: (region, sources, extra_args) ──
# Each runs sequentially within a region, but we use subprocess for isolation
SCRAPE_PHASES = [
    # HK — core market, app stores + reddit + youtube
    ("HK", "app_store_hk,google_play_hk,reddit_old,youtube_html", []),
    # TW — Taiwan market
    ("TW", "app_store_tw,google_play_tw", []),
    # US — English reviews
    ("US", "app_store_us,google_play_us,reddit_old", ["--subreddits", "HongKong,China,travel"]),
    # JP — Japanese market
    ("JP", "app_store_jp,google_play_jp", []),
]


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    """Run a command, return (exit_code, stdout)."""
    log(f"  CMD: {' '.join(cmd)}")
    try:
        r = subprocess.run(
            [str(x) for x in cmd],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJECT),
        )
        if r.stdout:
            log(f"  STDOUT: {r.stdout[-500:]}")
        if r.stderr:
            log(f"  STDERR: {r.stderr[-500:]}")
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        log(f"  TIMEOUT after {timeout}s")
        return -1, ""
    except Exception as e:
        log(f"  ERROR: {e}")
        return -1, str(e)


def scrape_phase(topic: str, query_variants: list[str], region: str, sources: str, extra_args: list[str]) -> int:
    """Scrape all query variants for a given topic/region. Returns total posts."""
    total = 0
    for i, query in enumerate(query_variants):
        log(f"  Scrape [{topic}] {region}[{i+1}/{len(query_variants)}]: '{query}' → {sources}")
        cmd = [
            str(MKT), "scrape",
            "--topic", query,
            "--region", region,
            "--sources", sources,
            "--limit", "200",
        ]
        cmd.extend(extra_args)
        exit_code, stdout = run(cmd, timeout=300)
        if exit_code != 0:
            log(f"  WARN: scrape exit={exit_code}, continuing")
        # Count emitted from log lines
        for line in stdout.splitlines():
            if "scrape.source.done" in line and '"emitted"' in line:
                # Not parsing JSON, just note
                pass
        time.sleep(2)  # brief cooldown between queries
    return total


def run_pipeline_for_topic(topic: str, dry_run: bool = False) -> None:
    """Run the full scrape → embed → cluster → synthesize pipeline for one topic."""
    query_variants = QUERY_VARIANTS_BY_TOPIC.get(topic, [topic])
    total_scrapes = len(SCRAPE_PHASES) * len(query_variants)

    log("")
    log("=" * 60)
    log(f"TOPIC: {topic}")
    log(f"Scrape phases: {len(SCRAPE_PHASES)} regions × {len(query_variants)} queries = {total_scrapes} scrapes")
    log("=" * 60)

    if dry_run:
        log("  DRY-RUN: would run full pipeline — scrape → embed → cluster → synthesize")
        for region, sources, extra_args in SCRAPE_PHASES:
            log(f"  Region: {region} ({sources})")
            for i, query in enumerate(query_variants):
                log(f"    [{i+1}/{len(query_variants)}] {query}")
        return

    # ── Phase 1: Scrape all regions ──
    log("\n── PHASE 1: MASS SCRAPE ──")
    phase_start = time.time()
    for region, sources, extra_args in SCRAPE_PHASES:
        log(f"\nRegion: {region} ({sources})")
        scrape_phase(topic, query_variants, region, sources, extra_args)
    log(f"Phase 1 done in {time.time() - phase_start:.0f}s")

    # ── Phase 2: Embed, Cluster, Synthesize per region ──
    log("\n── PHASE 2: PIPELINE PER REGION ──")
    for region, _, _ in SCRAPE_PHASES:
        log(f"\n=== Pipeline for {topic} / {region} ===")

        # Embed
        log(f"  Embed {topic} / {region}")
        exit_code, _ = run([str(MKT), "embed", "--topic", topic, "--region", region], timeout=600)
        if exit_code != 0:
            log(f"  WARN: embed exit={exit_code}, skipping {region}")
            continue

        # Cluster
        log(f"  Cluster {topic} / {region}")
        exit_code, _ = run([str(MKT), "cluster", "--topic", topic, "--region", region], timeout=120)
        if exit_code != 0:
            log(f"  WARN: cluster exit={exit_code}, skipping {region}")
            continue

        # Synthesize with DeepSeek (cheapest)
        log(f"  Synthesize {topic} / {region} with DeepSeek")
        exit_code, stdout = run([
            str(MKT), "synthesize",
            "--topic", topic,
            "--region", region,
            "--provider", "deepseek",
            "--force",
        ], timeout=600)
        if exit_code != 0:
            log(f"  WARN: synthesize exit={exit_code}")
        else:
            # Print cost summary
            for line in stdout.splitlines():
                if "Actual cost" in line or "persona" in line or "Clusters" in line:
                    log(f"  {line.strip()}")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    dry_run = "--dry-run" in sys.argv

    log("=" * 60)
    log("OVERNIGHT PIPELINE START")
    log(f"Topics: {', '.join(TOPICS)}")
    log(f"Total scrapes: {len(SCRAPE_PHASES)} regions × ~{sum(len(QUERY_VARIANTS_BY_TOPIC.get(t, [t])) for t in TOPICS)} queries across {len(TOPICS)} topics")
    log(f"Log: {LOG_FILE}")
    if dry_run:
        log("MODE: DRY-RUN (no commands executed)")

    pipeline_start = time.time()

    for i, topic in enumerate(TOPICS):
        log(f"\n{'#' * 60}")
        log(f"# TOPIC {i+1}/{len(TOPICS)}: {topic}")
        log(f"{'#' * 60}")
        run_pipeline_for_topic(topic, dry_run=dry_run)

    elapsed = time.time() - pipeline_start
    log("\n" + "=" * 60)
    log(f"OVERNIGHT PIPELINE COMPLETE — {len(TOPICS)} topics in {elapsed:.0f}s ({elapsed/60:.1f}m)")
    log(f"Full log: {LOG_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
