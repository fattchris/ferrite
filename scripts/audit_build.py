#!/usr/bin/env python3
"""Automated spec audit for Ferrite.

Scans ~/ferrite/src/ferrite/ against ~/ferrite-spec-v3.md gap checklist.
Outputs JSON to stdout with status per gap. Designed to be called by
continue_build.py and cron jobs.

Usage:
    python scripts/audit_build.py          # JSON to stdout
    python scripts/audit_build.py --pretty  # Human-readable
"""

import json
import re
import sys
from pathlib import Path

FERRITE_ROOT = Path.home() / "ferrite"
SRC_DIR = FERRITE_ROOT / "src" / "ferrite"
SPEC_PATH = Path.home() / "ferrite-spec-v3.md"
TESTS_DIR = FERRITE_ROOT / "tests"
CI_PATH = FERRITE_ROOT / ".github" / "workflows" / "ci.yml"
FERRITE_YAML = FERRITE_ROOT / "ferrite.yaml"


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def _read_all_src() -> dict[str, str]:
    """Read all .py files in src/ferrite/ into {filename: content}."""
    result = {}
    if not SRC_DIR.exists():
        return result
    for f in SRC_DIR.iterdir():
        if f.suffix == ".py":
            result[f.name] = f.read_text()
    return result


def _all_src_text() -> str:
    return "\n".join(_read_all_src().values())


# ─── Gap checks ───────────────────────────────────────────────────────

def check_mcp_http_transport() -> bool:
    """§4.1: MCP HTTP transport at /mcp/ (not just stdio)."""
    mcp = _read(SRC_DIR / "mcp_server.py")
    api = _read(SRC_DIR / "api.py")
    # Look for HTTP transport, streamable_http, /mcp/ route, or ASGI mount
    return any(marker in mcp + api for marker in [
        "streamable_http", "StreamableHTTP", "/mcp/", "mount_mcp",
        "http_transport", "ASGITransport", "FastMCP",
    ])


def check_get_provenance() -> bool:
    """§4.2: get_provenance MCP tool."""
    mcp = _read(SRC_DIR / "mcp_server.py")
    query = _read(SRC_DIR / "query.py")
    all_text = mcp + query + _all_src_text()
    # Must have both the tool registration and a query function
    has_tool = "get_provenance" in all_text
    has_query = "provenance" in all_text.lower() and (
        "SOURCED_FROM" in all_text or "def get_provenance" in all_text
    )
    return has_tool and has_query


def check_list_episodes() -> bool:
    """§4.2: list_episodes MCP tool."""
    mcp = _read(SRC_DIR / "mcp_server.py")
    all_text = _all_src_text()
    return "list_episodes" in all_text or "list_episodes" in mcp


def check_ci_match_ban() -> bool:
    """§6.3: CI grep test banning raw MATCH strings in handler code."""
    ci = _read(CI_PATH)
    # Must have the check_raw_match step in CI
    return "check_raw_match" in ci and "MATCH" in ci


def check_ferrite_yaml() -> bool:
    """§15.2: ferrite.yaml config file exists and has required sections."""
    if not FERRITE_YAML.exists():
        return False
    content = _read(FERRITE_YAML)
    required_sections = ["server:", "llm:", "embedder:", "database:", "circuit_breaker:", "eval:", "rrf:", "backup:"]
    return all(s in content for s in required_sections)


def check_sqlite_keys() -> bool:
    """§6.1: SQLite keys.db for per-agent API keys."""
    ks = _read(SRC_DIR / "key_store.py")
    return "sqlite3" in ks and "api_keys" in ks


def check_key_mgmt_api() -> bool:
    """§6.2: Key management API endpoints."""
    api = _read(SRC_DIR / "api.py")
    return "/keys" in api and "revoke" in api.lower()


def check_ryow() -> bool:
    """§6.4: Read-Your-Own-Writes (LRU pending_ingestion)."""
    return "pending_ingestion" in _all_src_text()


def check_redis_consumer() -> bool:
    """§7.1: Redis queue + in-proc async consumer."""
    ing = _read(SRC_DIR / "ingestion.py")
    return "start_consumer" in ing and "asyncio" in ing


def check_session_gate() -> bool:
    """§7.2.1: Session quality gate."""
    qg = _read(SRC_DIR / "quality_gates.py")
    return "session_gate" in qg


def check_supersedes() -> bool:
    """§7.2.3: SUPERSEDES edge for functional predicates."""
    temp = _read(SRC_DIR / "temporal.py")
    ing = _read(SRC_DIR / "ingestion.py")
    return "SUPERSEDES" in temp and "functional" in temp + ing


def check_backup_scripts() -> bool:
    """§10: Backup/restore scripts are non-stubs."""
    backup = _read(FERRITE_ROOT / "scripts" / "backup.sh")
    restore = _read(FERRITE_ROOT / "scripts" / "restore.sh")
    return len(backup) > 200 and len(restore) > 200


def check_tests_pass() -> dict:
    """Run pytest -q and return results."""
    import subprocess
    try:
        result = subprocess.run(
            ["uv", "run", "pytest", "-q", "--tb=no", "-q"],
            cwd=str(FERRITE_ROOT),
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout + result.stderr
        # Parse "X passed, Y failed"
        passed = len(re.findall(r"(\d+) passed", output))
        failed = len(re.findall(r"(\d+) (?:failed|error)", output))
        nums = re.findall(r"(\d+) passed", output)
        total_passed = int(nums[0]) if nums else 0
        nums_f = re.findall(r"(\d+) (?:failed|error)", output)
        total_failed = int(nums_f[0]) if nums_f else 0
        return {
            "passed": total_passed,
            "failed": total_failed,
            "exit_code": result.returncode,
            "status": "pass" if result.returncode == 0 else "fail"
        }
    except Exception as e:
        return {"error": str(e), "status": "unknown"}


# ─── Gap registry ────────────────────────────────────────────────────

GAPS = [
    {
        "id": "GAP-001",
        "spec": "§4.1",
        "title": "MCP HTTP transport at /mcp/",
        "check": check_mcp_http_transport,
        "build_instructions": (
            "Add HTTP transport to the MCP server. The spec requires both HTTP at /mcp/ "
            "and stdio. Currently only stdio is implemented. Use FastMCP's streamable_http "
            "transport or mount the MCP server at /mcp/ in the FastAPI app. "
            "File: src/ferrite/mcp_server.py (and optionally src/ferrite/api.py for mounting)."
        ),
    },
    {
        "id": "GAP-002",
        "spec": "§4.2",
        "title": "get_provenance MCP tool",
        "check": check_get_provenance,
        "build_instructions": (
            "Add get_provenance MCP tool. Returns the provenance chain: "
            "agent → channel → session → episode → source for a given fact ID. "
            "Cross-namespace chains truncate with redacted_beyond_this_point. "
            "Files: src/ferrite/mcp_server.py (register tool), src/ferrite/query.py (add get_provenance function). "
            "Query: traverse SOURCED_FROM edges from Fact → Episode → Session, "
            "collect agent/channel metadata."
        ),
    },
    {
        "id": "GAP-003",
        "spec": "§4.2",
        "title": "list_episodes MCP tool",
        "check": check_list_episodes,
        "build_instructions": (
            "Add list_episodes MCP tool. Returns recent episodes ingested. "
            "Parameters: limit (default 20), since (datetime, optional). "
            "File: src/ferrite/mcp_server.py (register tool), src/ferrite/query.py (add list_episodes function). "
            "Query: MATCH (e:Episode) WHERE e.recorded_at >= $since "
            "RETURN e ORDER BY e.recorded_at DESC LIMIT $limit"
        ),
    },
    {
        "id": "GAP-004",
        "spec": "§6.3",
        "title": "CI grep test banning raw MATCH strings",
        "check": check_ci_match_ban,
        "build_instructions": (
            "Add a CI step in .github/workflows/ci.yml that greps src/ferrite/*.py "
            "for raw 'MATCH' strings (banned per §6.3 — all Cypher must go through "
            "a query builder that injects namespace filters). "
            "The grep should fail the build if raw MATCH is found in handler code "
            "(excluding test files and the query.py builder itself). "
            "Example: grep -rn 'MATCH' src/ferrite/*.py | grep -v query.py | grep -v test_ && exit 1 || true"
        ),
    },
    {
        "id": "GAP-005",
        "spec": "§6.1",
        "title": "SQLite keys.db for per-agent API keys",
        "check": check_sqlite_keys,
        "build_instructions": "Already implemented in key_store.py.",
    },
    {
        "id": "GAP-006",
        "spec": "§6.2",
        "title": "Key management API (POST /keys, revoke, list)",
        "check": check_key_mgmt_api,
        "build_instructions": "Already implemented in api.py.",
    },
    {
        "id": "GAP-007",
        "spec": "§6.4",
        "title": "Read-Your-Own-Writes (LRU pending_ingestion)",
        "check": check_ryow,
        "build_instructions": "Already implemented in ingestion.py.",
    },
    {
        "id": "GAP-008",
        "spec": "§7.1",
        "title": "Redis queue + in-proc async consumer",
        "check": check_redis_consumer,
        "build_instructions": "Already implemented in ingestion.py.",
    },
    {
        "id": "GAP-009",
        "spec": "§7.2.1",
        "title": "Session quality gate (≥2 turns, error state check)",
        "check": check_session_gate,
        "build_instructions": "Already implemented in quality_gates.py.",
    },
    {
        "id": "GAP-010",
        "spec": "§7.2.3",
        "title": "SUPERSEDES edge for functional predicates",
        "check": check_supersedes,
        "build_instructions": "Already implemented in temporal.py + ingestion.py.",
    },
    {
        "id": "GAP-011",
        "spec": "§10",
        "title": "Backup/restore scripts (non-stub)",
        "check": check_backup_scripts,
        "build_instructions": "Already implemented in scripts/backup.sh + restore.sh.",
    },
    {
        "id": "GAP-012",
        "spec": "§15.2",
        "title": "ferrite.yaml config file",
        "check": check_ferrite_yaml,
        "build_instructions": "Already implemented at ~/ferrite/ferrite.yaml.",
    },
]


def run_audit(include_tests: bool = False) -> dict:
    """Run all gap checks. Returns structured audit result."""
    results = []
    for gap in GAPS:
        try:
            resolved = gap["check"]()
        except Exception:
            resolved = False
        results.append({
            "id": gap["id"],
            "spec": gap["spec"],
            "title": gap["title"],
            "resolved": resolved,
            "build_instructions": gap["build_instructions"],
        })

    open_gaps = [r for r in results if not r["resolved"]]
    resolved_gaps = [r for r in results if r["resolved"]]

    audit = {
        "audit_time": __import__("datetime").datetime.now().isoformat(),
        "total_gaps": len(results),
        "resolved": len(resolved_gaps),
        "remaining": len(open_gaps),
        "open_gaps": open_gaps,
        "resolved_gaps": resolved_gaps,
    }

    if include_tests:
        audit["tests"] = check_tests_pass()

    return audit


if __name__ == "__main__":
    pretty = "--pretty" in sys.argv
    include_tests = "--tests" in sys.argv
    audit = run_audit(include_tests=include_tests)

    if pretty:
        print(f"Ferrite Build Audit — {audit['audit_time']}")
        print(f"{'='*50}")
        print(f"Total gaps: {audit['total_gaps']}")
        print(f"Resolved:   {audit['resolved']}")
        print(f"Remaining:  {audit['remaining']}")
        print(f"{'='*50}")
        if audit["open_gaps"]:
            print("\nOPEN GAPS:")
            for g in audit["open_gaps"]:
                print(f"  ❌ {g['id']} ({g['spec']}) — {g['title']}")
        if audit["resolved_gaps"]:
            print("\nRESOLVED:")
            for g in audit["resolved_gaps"]:
                print(f"  ✅ {g['id']} ({g['spec']}) — {g['title']}")
        if "tests" in audit:
            t = audit["tests"]
            print(f"\nTests: {t.get('passed', 0)} passed, {t.get('failed', 0)} failed")
    else:
        print(json.dumps(audit, indent=2))
