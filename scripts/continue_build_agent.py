#!/usr/bin/env python3
"""Agent-driven Ferrite build continuation.

This script is designed to be run BY the Hermes agent (not standalone).
It outputs structured instructions for the agent to follow:

1. Run the audit to find open gaps
2. For each open gap: dispatch a delegate_task subagent to build it
3. Verify each gap after the subagent completes
4. Run tests
5. Fix any failing tests
6. Report final status

The agent should call this script, read the output, then execute
the delegate_task calls with the provided instructions.

Usage from Hermes agent:
    cd ~/ferrite && uv run python scripts/continue_build_agent.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit_build import run_audit, GAPS

FERRITE_ROOT = Path.home() / "ferrite"
BUILD_STATE = FERRITE_ROOT / "BUILD_STATE.json"


def main():
    # Run audit
    audit = run_audit()
    open_gaps = audit["open_gaps"]
    resolved = audit["resolved"]
    total = audit["total_gaps"]

    # Load state for attempt tracking
    state = {}
    if BUILD_STATE.exists():
        state = json.loads(BUILD_STATE.read_text())

    # Build action plan for agent
    plan = {
        "timestamp": datetime.now().isoformat(),
        "audit_summary": {
            "total": total,
            "resolved": resolved,
            "remaining": len(open_gaps),
        },
        "actions": [],
    }

    if not open_gaps:
        plan["actions"].append({
            "type": "run_tests",
            "command": "cd ~/ferrite && uv run pytest -q --tb=short",
            "description": "All spec gaps resolved. Run test suite to verify.",
        })
        plan["actions"].append({
            "type": "fix_failing_tests",
            "description": "If any tests fail, fix them using delegate_task.",
        })
        plan["final_status"] = "all_gaps_resolved"
    else:
        # Sort gaps: fewest attempts first
        gap_attempts = {}
        for gid, gs in state.get("gaps", {}).items():
            gap_attempts[gid] = gs.get("attempts", 0)

        sorted_gaps = sorted(open_gaps, key=lambda g: (gap_attempts.get(g["id"], 0), g["id"]))

        for gap in sorted_gaps:
            attempts = gap_attempts.get(gap["id"], 0)
            plan["actions"].append({
                "type": "delegate_task",
                "gap_id": gap["id"],
                "spec": gap["spec"],
                "title": gap["title"],
                "attempts_so_far": attempts,
                "goal": (
                    f"Build Ferrite spec gap {gap['id']} ({gap['spec']}: {gap['title']}). "
                    f"Working directory: ~/ferrite. "
                    f"Instructions: {gap['build_instructions']} "
                    f"After implementation, verify the change works. "
                    f"Do NOT modify test fixtures or wipe Neo4j data. "
                    f"Only modify files relevant to this gap. "
                    f"Run the specific audit check after: "
                    f"cd ~/ferrite && uv run python scripts/audit_build.py"
                ),
                "context": (
                    f"Ferrite is a temporal knowledge graph memory system. "
                    f"Spec: ~/ferrite-spec-v3.md. Source: ~/ferrite/src/ferrite/. "
                    f"Tests: ~/ferrite/tests/. Use uv run pytest to test. "
                    f"This is gap {gap['id']} out of {total}. "
                    f"Previous attempts: {attempts}."
                ),
            })

        plan["actions"].append({
            "type": "run_tests",
            "command": "cd ~/ferrite && uv run pytest -q --tb=short",
            "description": "After all gaps are built, run full test suite.",
        })
        plan["actions"].append({
            "type": "update_build_state",
            "command": "cd ~/ferrite && uv run python scripts/audit_build.py --pretty",
            "description": "Run final audit to verify all gaps resolved.",
        })
        plan["final_status"] = "gaps_remaining"

    # Print as JSON for agent consumption
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
