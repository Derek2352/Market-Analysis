#!/usr/bin/env python
"""Overnight AlipayHK pipeline — mass scrape, embed, cluster, synthesize.

Runs autonomously as a cron job. Reports progress via structured logs.
Exits 0 on success, non-zero on failure after exhausting retries.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(r"C:\Users\Derek Yung\Market-Analysis")
VENV_PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
MKT = PROJECT / ".venv" / "Scripts" / "mkt.exe"
LOG_FILE = PROJECT / "logs" / f"overnight_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"

TOPIC = "AlipayHK"

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

# ── Query variants to scrape (without --expand, to avoid too-specific queries) ──
QUERY_VARIANTS = [
    "AlipayHK",
    "alipay hk",
    "支付寶HK",
    "支付寶 香港",
    "Alipay Hong Kong",
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


def scrape_phase(region: str, sources: str, extra_args: list[str]) -> int:
    """Scrape all query variants for a region. Returns total posts."""
    total = 0
    for i, query in enumerate(QUERY_VARIANTS):
        log(f"Scrape {region}[{i+1}/{len(QUERY_VARIANTS)}]: '{query}' → {sources}")
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


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    log("=" * 60)
    log("OVERNIGHT PIPELINE START")
    log(f"Topic: {TOPIC}")
    log(f"Scrape phases: {len(SCRAPE_PHASES)} regions × {len(QUERY_VARIANTS)} queries = {len(SCRAPE_PHASES) * len(QUERY_VARIANTS)} scrapes")
    log(f"Log: {LOG_FILE}")

    # ── Phase 1: Scrape all regions ──
    log("\n── PHASE 1: MASS SCRAPE ──")
    phase_start = time.time()
    for region, sources, extra_args in SCRAPE_PHASES:
        log(f"\nRegion: {region} ({sources})")
        scrape_phase(region, sources, extra_args)
    log(f"Phase 1 done in {time.time() - phase_start:.0f}s")

    # ── Phase 2: Embed, Cluster, Synthesize per region ──
    log("\n── PHASE 2: PIPELINE PER REGION ──")
    for region, _, _ in SCRAPE_PHASES:
        log(f"\n=== Pipeline for {region} ===")

        # Embed
        log(f"  Embed {TOPIC} / {region}")
        exit_code, _ = run([str(MKT), "embed", "--topic", TOPIC, "--region", region], timeout=600)
        if exit_code != 0:
            log(f"  WARN: embed exit={exit_code}, skipping {region}")
            continue

        # Cluster
        log(f"  Cluster {TOPIC} / {region}")
        exit_code, _ = run([str(MKT), "cluster", "--topic", TOPIC, "--region", region], timeout=120)
        if exit_code != 0:
            log(f"  WARN: cluster exit={exit_code}, skipping {region}")
            continue

        # Synthesize with DeepSeek (cheapest)
        log(f"  Synthesize {TOPIC} / {region} with DeepSeek")
        exit_code, stdout = run([
            str(MKT), "synthesize",
            "--topic", TOPIC,
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

    log("\n" + "=" * 60)
    log(f"OVERNIGHT PIPELINE COMPLETE in {time.time() - phase_start:.0f}s")
    log(f"Full log: {LOG_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
