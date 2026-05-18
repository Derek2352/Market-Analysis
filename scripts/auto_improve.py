#!/usr/bin/env python
"""Auto-improve checker — detects idle state and dispatches next task.

Called every 15 min by cron. The cron agent runs this, then follows the
printed instructions.

Output modes:
  BUSY: <what's running>       → agent reports status, does nothing else
  IDLE: <task JSON>            → agent implements the task
  DONE: <summary>              → all tasks complete, agent reports victory
  STALE: <task_id>             → previous task likely crashed, agent decides
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(r"C:\Users\Derek Yung\Market-Analysis")
QUEUE_FILE = PROJECT / "scripts" / "task_queue.json"
STALE_TIMEOUT_MINUTES = 30  # if in_progress > this, consider crashed


def check_running() -> list[str]:
    """Check if any Market-Analysis processes are running."""
    running = []
    try:
        # Windows: tasklist to find python processes with mkt/scrape/pipeline
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        if "python.exe" in r.stdout:
            # Could be anything — check command lines via wmic
            r2 = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine,ProcessId", "/FORMAT:CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            for line in r2.stdout.splitlines():
                line = line.strip().strip('"')
                if not line:
                    continue
                lower = line.lower()
                if any(kw in lower for kw in ["market-analysis", "mkt", "scrape",
                                               "overnight_pipeline", "embed", "cluster",
                                               "synthesize", "auto_improve"]):
                    # Extract PID
                    parts = line.split(",")
                    if len(parts) >= 2:
                        pid = parts[-1].strip().strip('"')
                        cmd = parts[0].strip().strip('"') if len(parts) > 2 else line
                        # Truncate long command lines
                        if len(cmd) > 120:
                            cmd = cmd[:117] + "..."
                        running.append(f"PID {pid}: {cmd}")
    except Exception:
        pass
    return running


def load_queue() -> dict:
    """Load task queue."""
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    return {"tasks": [], "completed": [], "stats": {}}


def save_queue(q: dict) -> None:
    """Save task queue."""
    pending = sum(1 for t in q["tasks"] if t["status"] == "pending")
    in_progress = sum(1 for t in q["tasks"] if t["status"] == "in_progress")
    completed = len(q["completed"])
    q["stats"] = {"total": len(q["tasks"]), "pending": pending,
                   "in_progress": in_progress, "completed": completed}
    QUEUE_FILE.write_text(json.dumps(q, indent=2, ensure_ascii=False), encoding="utf-8")


def handle_stale(q: dict) -> str | None:
    """Check for stale in_progress tasks. Returns STALE:<id> if found."""
    now = datetime.now(timezone.utc)
    for t in q["tasks"]:
        if t["status"] == "in_progress":
            started_str = t.get("started_at", "")
            if started_str:
                try:
                    started = datetime.fromisoformat(started_str)
                    elapsed = (now - started).total_seconds() / 60
                    if elapsed > STALE_TIMEOUT_MINUTES:
                        t["status"] = "pending"
                        t.pop("started_at", None)
                        save_queue(q)
                        return f"STALE:{t['id']} (in_progress for {elapsed:.0f}min — reset to pending)"
                except ValueError:
                    pass
    return None


def main() -> str:
    action = sys.argv[1] if len(sys.argv) > 1 else "check"

    if action == "--complete":
        task_id = sys.argv[2]
        q = load_queue()
        for t in q["tasks"]:
            if t["id"] == task_id:
                t["status"] = "completed"
                t["completed_at"] = datetime.now(timezone.utc).isoformat()
                t.pop("started_at", None)
                q["completed"].append({"id": t["id"], "title": t["title"],
                                        "completed_at": t["completed_at"]})
                save_queue(q)
                print(f"DONE: Completed '{t['title']}'")
                # Check if all done
                remaining = [t for t in q["tasks"] if t["status"] == "pending"]
                if not remaining:
                    pending_total = len(q["tasks"])
                    print(f"ALL_DONE: {len(q['completed'])}/{pending_total} tasks completed")
                return ""
        print(f"ERROR: Task '{task_id}' not found")
        return ""

    if action == "--fail":
        task_id = sys.argv[2]
        q = load_queue()
        for t in q["tasks"]:
            if t["id"] == task_id:
                t["status"] = "pending"
                t.pop("started_at", None)
                save_queue(q)
                print(f"RESET: '{t['title']}' back to pending")
                return ""
        print(f"ERROR: Task '{task_id}' not found")
        return ""

    if action == "--skip":
        task_id = sys.argv[2]
        q = load_queue()
        for t in q["tasks"]:
            if t["id"] == task_id:
                t["status"] = "skipped"
                t["skipped_at"] = datetime.now(timezone.utc).isoformat()
                t.pop("started_at", None)
                save_queue(q)
                print(f"SKIPPED: '{t['title']}'")
                return ""
        print(f"ERROR: Task '{task_id}' not found")
        return ""

    # ── CHECK mode ──
    running = check_running()

    # Filter out auto_improve.py itself (current process)
    running = [r for r in running if "auto_improve" not in r]

    if running:
        lines = [f"BUSY: {len(running)} process(es) running"]
        for r in running[:5]:
            lines.append(f"  {r}")
        print("\n".join(lines))
        return ""

    # Idle — check for stale tasks first
    q = load_queue()
    stale = handle_stale(q)
    if stale:
        print(stale)
        print("(task reset to pending — available for next check)")
        # Fall through to dispatch
        q = load_queue()  # reload after stale fix

    # Find highest priority pending task
    pending = [t for t in q["tasks"] if t["status"] == "pending"]
    pending.sort(key=lambda t: t["priority"])

    if not pending:
        print("DONE: All improvement tasks complete!")
        print(f"Completed: {len(q['completed'])} tasks")
        for c in q["completed"][-5:]:
            print(f"  ✓ {c['title']}")
        return ""

    # Dispatch next task
    task = pending[0]
    task["status"] = "in_progress"
    task["started_at"] = datetime.now(timezone.utc).isoformat()
    save_queue(q)

    print(f"IDLE: Dispatching task #{task['priority']}")
    print(f"TASK_ID: {task['id']}")
    print(f"TITLE: {task['title']}")
    print(f"CATEGORY: {task['category']}")
    print(f"FILES: {', '.join(task['files'])}")
    print(f"TEST: {task['test_cmd']}")
    print(f"DESCRIPTION: {task['description']}")
    print(f"\nINSTRUCTIONS:")
    print(f"1. Implement: {task['description']}")
    print(f"2. Test: {task['test_cmd']}")
    print(f"3. Commit + push with message: {task['id']}: {task['title']}")
    print(f"4. Mark complete: .venv/Scripts/python.exe scripts/auto_improve.py --complete {task['id']}")
    return ""


if __name__ == "__main__":
    main()
