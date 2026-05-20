#!/usr/bin/env python
"""Delta scrape — quick scrape of AlipayHK across top sources to accumulate fresh data.

Runs every 2h. Silent when nothing new (stdout empty).
"""
import os
import subprocess, sys, time
from pathlib import Path

# Resolve project root from this script's location so the same file works
# on any machine + any OS.
PROJECT = Path(__file__).resolve().parent.parent
VENV_BIN = PROJECT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
MKT = VENV_BIN / ("mkt.exe" if os.name == "nt" else "mkt")

# Quick hits — top sources, no expansion, fresh data
JOBS = [
    ("HK", "app_store_hk,google_play_hk", "AlipayHK", []),
    ("TW", "app_store_tw,google_play_tw", "AlipayHK", []),
    ("US", "app_store_us,google_play_us", "AlipayHK", []),
    ("JP", "app_store_jp,google_play_jp", "AlipayHK", []),
    ("HK", "reddit_old,youtube_html", "AlipayHK", ["--subreddits", "HongKong,China,HongKongTravel"]),
    ("HK", "app_store_hk,google_play_hk", "支付寶 香港", []),
]

total_new = 0
for region, sources, topic, extra in JOBS:
    cmd = [
        str(MKT), "scrape",
        "--topic", topic,
        "--region", region,
        "--sources", sources,
        "--limit", "200",
    ]
    cmd.extend(extra)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(PROJECT))
    if r.returncode == 0:
        total_new += 1
    time.sleep(1)

if total_new > 0:
    print(f"Delta scrape: {total_new}/{len(JOBS)} jobs completed OK")
