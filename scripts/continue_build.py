#!/usr/bin/env python3
"""Ferrite Build Continuation Engine.

This script is the core of the "keep building until done" system.
It runs the audit, picks the next open gap, dispatches a delegate_task
subagent to build it, verifies the result, and updates BUILD_STATE.json.

Designed to be called:
  1. Manually: `python scripts/continue_build.py`
  2. Via cron: scheduled every 2 hours to keep building autonomously
  3. Via Hermes cron job (no_agent=False)

The script NEVER stops at a single blocked gap. If a gap build fails,
it records the failure and moves to the next open gap. It only reports
"complete" when ALL gaps are resolved AND tests pass.

Exit codes:
  0 — all gaps resolved, tests pass
  1 — some gaps still open (work was done, more needed)
  2 — no gaps open but tests failing
  3 — audit error
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Make audit_build importable
sys.path.insert(0, str(Path(__file__).parent))
from audit_build import run_audit, GAPS

FERRITE_ROOT = Path.home() / "ferrite"
BUILD_STATE = FERRITE_ROOT / "BUILD_STATE.json"
REPORT_DIR = FERRITE_ROOT / "build_reports"
REPORT_DIR.mkdir(exist_ok=True)


def load_state() -> dict:
    if BUILD_STATE.exists():
        return json.loads(BUILD_STATE.read_text())
    return {"gaps": {}, "test_state": {}}


def save_state(state: dict):
    state["last_updated"] = datetime.now().isoformat()
    BUILD_STATE.write_text(json.dumps(state, indent=2))


def run_tests() -> dict:
    """Run pytest and return results."""
    try:
        result = subprocess.run(
            ["uv", "run", "pytest", "-q", "--tb=line", "-q"],
            cwd=str(FERRITE_ROOT),
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout + result.stderr
        import re
        passed = 0
        failed = 0
        skipped = 0
        m = re.search(r"(\d+) passed", output)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", output)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+) skipped", output)
        if m:
            skipped = int(m.group(1))
        # Extract failing test names
        failing = re.findall(r"FAILED (tests/\S+)", output)
        return {
            "total": passed + failed + skipped,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "failing_tests": failing,
            "exit_code": result.returncode,
            "status": "pass" if result.returncode == 0 else "fail",
            "output": output[-2000:] if len(output) > 2000 else output,
        }
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "status": "timeout"}
    except Exception as e:
        return {"error": str(e), "status": "unknown"}


def get_build_instructions(gap_id: str) -> str:
    """Get build instructions for a gap."""
    for gap in GAPS:
        if gap["id"] == gap_id:
            return gap["build_instructions"]
    return "No instructions found."


def write_report(report: dict) -> Path:
    """Write a build report to build_reports/."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"build_{ts}.json"
    path.write_text(json.dumps(report, indent=2))
    return path


def build_gap_via_hermes(gap: dict) -> dict:
    """Build a gap using hermes_tools delegate_task.

    Since this runs as a Python script (not inside the agent loop),
    we can't call delegate_task directly. Instead, we write a build
    instruction file that the Hermes agent or cron job can pick up.

    For direct agent-driven continuation, this function should be
    called from within the agent context (see continue_build_agent.py).
    """
    # Write build task file for agent pickup
    task = {
        "gap_id": gap["id"],
        "spec": gap["spec"],
        "title": gap["title"],
        "instructions": gap["build_instructions"],
        "created": datetime.now().isoformat(),
        "status": "pending_build",
    }
    task_path = FERRITE_ROOT / "build_reports" / f"task_{gap['id']}.json"
    task_path.write_text(json.dumps(task, indent=2))
    return {
        "status": "task_written",
        "task_path": str(task_path),
        "message": f"Build task for {gap['id']} written to {task_path}. "
                   f"Run continue_build_agent.py from Hermes agent context to execute."
    }


def verify_gap(gap_id: str) -> bool:
    """Re-run audit for a specific gap."""
    audit = run_audit()
    for g in audit["open_gaps"] + audit["resolved_gaps"]:
        if g["id"] == gap_id:
            return g["resolved"]
    return False


def main():
    print(f" Ferrite Build Continuation Engine")
    print(f"   {datetime.now().isoformat()}")
    print()

    # 1. Run audit
    audit = run_audit()
    open_gaps = audit["open_gaps"]
    resolved_count = audit["resolved"]
    total = audit["total_gaps"]

    print(f"📊 Audit: {resolved_count}/{total} gaps resolved, {len(open_gaps)} remaining")

    # Load state
    state = load_state()

    # Update state with audit results
    for g in audit["open_gaps"] + audit["resolved_gaps"]:
        gid = g["id"]
        if gid not in state["gaps"]:
            state["gaps"][gid] = {
                "spec": g["spec"],
                "title": g["title"],
                "status": "unknown",
                "attempts": 0,
                "last_attempt": None,
                "last_result": None,
            }
        state["gaps"][gid]["status"] = "resolved" if g["resolved"] else "open"
        if g["resolved"] and not state["gaps"][gid].get("last_result"):
            state["gaps"][gid]["last_result"] = "Audit check passed"

    # 2. If no open gaps, run tests
    if not open_gaps:
        print(f"\n✅ All spec gaps resolved! Running tests...")
        test_results = run_tests()
        state["test_state"] = {
            "last_run": datetime.now().isoformat(),
            **{k: v for k, v in test_results.items() if k != "output"}
        }
        save_state(state)

        if test_results["status"] == "pass":
            print(f"✅ Tests: {test_results['passed']} passed, {test_results['failed']} failed, {test_results.get('skipped', 0)} skipped")
            print(f"\n🎉 BUILD COMPLETE — all gaps resolved, all tests pass")
            write_report({
                "timestamp": datetime.now().isoformat(),
                "action": "build_complete",
                "audit": audit,
                "tests": test_results,
            })
            return 0
        else:
            print(f"❌ Tests: {test_results.get('passed', 0)} passed, {test_results.get('failed', 0)} failed")
            if test_results.get("failing_tests"):
                print(f"   Failing: {', '.join(test_results['failing_tests'][:5])}")
            print(f"\n⚠️  All gaps resolved but tests failing — need to fix test failures")
            write_report({
                "timestamp": datetime.now().isoformat(),
                "action": "tests_failing",
                "audit": audit,
                "tests": test_results,
            })
            return 2

    # 3. Pick next open gap (fewest attempts first, then by ID)
    open_with_state = []
    for g in open_gaps:
        gid = g["id"]
        gs = state["gaps"].get(gid, {"attempts": 0})
        open_with_state.append({
            **g,
            "attempts": gs.get("attempts", 0),
            "last_attempt": gs.get("last_attempt"),
        })

    open_with_state.sort(key=lambda x: (x["attempts"], x["id"]))
    next_gap = open_with_state[0]

    print(f"\n🎯 Next gap: {next_gap['id']} ({next_gap['spec']}) — {next_gap['title']}")
    print(f"   Attempts so far: {next_gap['attempts']}")

    # 4. Build it
    print(f"\n🔨 Building {next_gap['id']}...")
    result = build_gap_via_hermes(next_gap)

    # Update state
    gid = next_gap["id"]
    state["gaps"][gid]["attempts"] = state["gaps"][gid].get("attempts", 0) + 1
    state["gaps"][gid]["last_attempt"] = datetime.now().isoformat()
    state["gaps"][gid]["last_result"] = result.get("message", "task written")

    # 5. Verify
    print(f"\n🔍 Verifying {next_gap['id']}...")
    verified = verify_gap(gid)
    if verified:
        state["gaps"][gid]["status"] = "resolved"
        state["gaps"][gid]["last_result"] = "Verified resolved by audit"
        print(f"✅ {gid} verified resolved!")
    else:
        print(f"⚠️  {gid} still open after build attempt (task file written for agent)")
        print(f"   Task file: {result.get('task_path')}")

    save_state(state)

    # 6. Check remaining
    remaining = sum(1 for g in state["gaps"].values() if g.get("status") != "resolved")
    print(f"\n📊 Summary: {total - remaining}/{total} resolved, {remaining} still open")

    write_report({
        "timestamp": datetime.now().isoformat(),
        "action": "gap_build_attempt",
        "gap": next_gap,
        "result": result,
        "verified": verified,
        "remaining": remaining,
    })

    return 1 if remaining > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
