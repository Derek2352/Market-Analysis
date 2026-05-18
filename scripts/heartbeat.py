#!/usr/bin/env python
"""Heartbeat — checks pipeline progress and reports status.

Runs as cron no_agent script. Prints status to stdout for delivery.
Empty stdout when nothing notable → silent.
"""
import json, time
from pathlib import Path

DATA = Path(r"C:\Users\Derek Yung\Market-Analysis\data")
RAW = DATA / "raw" / "alipayhk"
PERSONAS = DATA / "personas" / "alipayhk"
CLUSTERS = DATA / "clusters" / "alipayhk"

status = {}

# Count raw posts per region
for region_dir in sorted(RAW.glob("*")):
    if not region_dir.is_dir():
        continue
    region = region_dir.name
    count = 0
    for f in region_dir.glob("*.json"):
        if f.name.endswith("._run.json"):
            continue
        try:
            with open(f) as fh:
                data = json.load(fh)
            if isinstance(data, list):
                count += len(data)
        except:
            pass
    if count > 0:
        status[region] = {"raw_posts": count}

# Count personas per region  
for region_dir in sorted(PERSONAS.glob("*")):
    if not region_dir.is_dir():
        continue
    region = region_dir.name
    personas = list(region_dir.glob("persona_*.json"))
    if region in status:
        status[region]["personas"] = len(personas)
    else:
        status[region] = {"personas": len(personas)}

# Count clusters
for region_dir in sorted(CLUSTERS.glob("*")):
    if not region_dir.is_dir():
        continue
    region = region_dir.name
    latest = sorted(region_dir.glob("clusters_*.json"))
    if latest:
        with open(latest[-1]) as f:
            cdata = json.load(f)
        if region in status:
            status[region]["clusters"] = len(cdata.get("clusters", []))
            status[region]["total_posts"] = cdata.get("total_posts", 0)
        else:
            status[region] = {
                "clusters": len(cdata.get("clusters", [])),
                "total_posts": cdata.get("total_posts", 0),
            }

if status:
    lines = [f"HB {time.strftime('%H:%M')}: "]
    for region, s in sorted(status.items()):
        parts = [f"{region}:"]
        if "raw_posts" in s:
            parts.append(f"{s['raw_posts']} posts")
        if "clusters" in s:
            parts.append(f"{s['clusters']} clusters")
        if "personas" in s:
            parts.append(f"{s['personas']} personas")
        lines.append("  " + " | ".join(parts))
    print("\n".join(lines))
