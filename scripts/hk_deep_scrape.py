#!/usr/bin/env python
"""Deep HK scrape — all working HK sources with query expansion.

Runs as cron no_agent script: stdout is delivered to Derek.
Empty stdout = silent (no results to report).
"""
import subprocess, sys
from pathlib import Path

PROJECT = Path(r"C:\Users\Derek Yung\Market-Analysis")
MKT = PROJECT / ".venv" / "Scripts" / "mkt.exe"

TOPICS = ["AlipayHK", "alipay hk", "支付寶 香港", "Alipay 香港", "支付寶HK"]
SOURCES = "app_store_hk,google_play_hk,reddit_old,youtube_html,lihkg"

results = []
for topic in TOPICS:
    cmd = [
        str(MKT), "scrape",
        "--topic", topic,
        "--region", "HK",
        "--sources", SOURCES,
        "--limit", "200",
        "--expand",
        "--accept-tos-risk",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(PROJECT))
    # Count emitted
    emitted = 0
    for line in r.stdout.splitlines():
        if "scrape.source.done" in line and '"emitted"' in line:
            import json
            try:
                obj = json.loads(line.strip().lstrip("{"))
            except:
                pass
    results.append((topic, r.returncode))

# Report only if something notable happened
new_results = [t for t, rc in results if rc == 0]
if new_results:
    topics_str = ", ".join(f"'{t}'" for t in new_results)
    print(f"HK deep scrape complete: {len(new_results)}/{len(TOPICS)} topics ok ({topics_str})")
