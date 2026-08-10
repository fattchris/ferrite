#!/usr/bin/env python3
"""Check for raw MATCH strings in handler code (§6.3).

The spec bans raw `MATCH` Cypher strings in handler code — all Cypher
should go through a query builder that injects namespace filters on
every Fact node pattern. This script enforces that ban in CI.

Files exempted:
  - query.py (the query builder itself — owns the MATCH strings)
  - schema.py (DDL — uses CREATE INDEX, not MATCH)
  - test_*.py (tests can use raw MATCH)
  - scripts/ (build scripts, not handler code)

Files currently in tech-debt allowlist (have raw MATCH, need refactoring
to route through query.py):
  - api.py
  - mcp_server.py
  - canonicalize.py
  - consolidator.py
  - ingestion.py
  - temporal.py
  - mental_models.py
  - tempr.py

The allowlist shrinks over time as files are refactored. When a file
is removed from the allowlist, any raw MATCH in it fails the check.

Usage:
    python scripts/check_raw_match.py            # check all
    python scripts/check_raw_match.py --strict   # fail on allowlisted too
    python scripts/check_raw_match.py --report   # show detailed report

Exit codes:
    0 — no new violations
    1 — new violations found (raw MATCH in non-allowlisted file)
"""

import re
import sys
from pathlib import Path

FERRITE_ROOT = Path(__file__).parent.parent
SRC_DIR = FERRITE_ROOT / "src" / "ferrite"

# Files that are the query builder/DDL — exempt from the ban
EXEMPT_FILES = {"query.py", "schema.py"}

# Files currently allowed to have raw MATCH (tech debt — shrink over time)
# These are internal pipeline modules that haven't been refactored yet.
ALLOWLIST = {
    "api.py",
    "mcp_server.py",
    "canonicalize.py",
    "consolidator.py",
    "ingestion.py",
    "temporal.py",
    "mental_models.py",
    "tempr.py",
    "vector_store.py",
    "observability.py",
}


def find_raw_match_in_file(filepath: Path) -> list[dict]:
    """Find raw MATCH strings in a Python file.
    
    A raw MATCH is a string literal containing 'MATCH' that is a Cypher
    query, not a comment or string that happens to contain the word.
    We look for MATCH at the start of a string (after stripping whitespace).
    """
    violations = []
    content = filepath.read_text()
    lines = content.split("\n")

    for i, line in enumerate(lines, 1):
        # Look for string literals containing MATCH (Cypher keyword)
        # Patterns: "MATCH ...", 'MATCH ...', triple-quoted, f-strings
        # Skip comments
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue

        # Check for MATCH in string literals (not in variable names or comments)
        # Match: "MATCH, 'MATCH, f"MATCH, f'MATCH, """MATCH
        matches = re.findall(
            r'''["']MATCH\b|f["']MATCH\b|"""MATCH\b|f"""MATCH\b''',
            line
        )
        if matches:
            # Also check for MATCH in concatenated strings like "MATCH ..."
            # that might be split across lines
            violations.append({
                "line": i,
                "content": line.strip()[:120],
            })

    return violations


def main():
    strict = "--strict" in sys.argv
    report_mode = "--report" in sys.argv

    all_violations = {}
    new_violations = {}  # In non-allowlisted files
    allowlisted_violations = {}  # In allowlisted files (tech debt)

    if not SRC_DIR.exists():
        print(f"ERROR: Source directory not found: {SRC_DIR}")
        return 3

    for filepath in SRC_DIR.iterdir():
        if not filepath.suffix == ".py":
            continue
        if filepath.name in EXEMPT_FILES:
            continue

        violations = find_raw_match_in_file(filepath)
        if not violations:
            continue

        all_violations[filepath.name] = violations

        if filepath.name in ALLOWLIST and not strict:
            allowlisted_violations[filepath.name] = violations
        else:
            new_violations[filepath.name] = violations

    # Report
    if report_mode or new_violations:
        print(f"\n§6.3 Raw MATCH Check — {Path(__file__).name}")
        print("=" * 60)

    if new_violations:
        print("\n❌ NEW VIOLATIONS (raw MATCH in non-allowlisted files):")
        for fname, viols in new_violations.items():
            print(f"\n  {fname}:")
            for v in viols:
                print(f"    L{v['line']}: {v['content']}")

    if allowlisted_violations and report_mode:
        print(f"\n⚠️  TECH DEBT (raw MATCH in allowlisted files — {sum(len(v) for v in allowlisted_violations.values())} total):")
        for fname, viols in sorted(allowlisted_violations.items()):
            print(f"  {fname}: {len(viols)} occurrences")

    if report_mode:
        total_new = sum(len(v) for v in new_violations.values())
        total_debt = sum(len(v) for v in allowlisted_violations.values())
        total_exempt = len(EXEMPT_FILES)
        print("\nSummary:")
        print(f"  Exempt files (query builder/DDL): {total_exempt}")
        print(f"  Allowlisted files (tech debt):   {len(allowlisted_violations)}")
        print(f"  New violations:                  {total_new}")
        print(f"  Total tech debt occurrences:     {total_debt}")

    if new_violations:
        print(f"\n❌ FAIL: {sum(len(v) for v in new_violations.values())} new violations found.")
        print("   Raw MATCH strings are banned in handler code (§6.3).")
        print("   Route Cypher through src/ferrite/query.py instead.")
        return 1

    if not report_mode:
        if allowlisted_violations:
            total_debt = sum(len(v) for v in allowlisted_violations.values())
            print(f"✅ No new violations. {total_debt} tech-debt occurrences in allowlisted files (use --report for details).")
        else:
            print("✅ No raw MATCH violations found.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
