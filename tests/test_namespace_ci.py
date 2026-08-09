"""CI test: namespace enforcement (§6.3).

Raw MATCH strings in handler code are banned; all Cypher must go
through a query builder that injects the namespace predicate on every
Fact node pattern, including traversal hops and get_entity edge expansion.

This test greps all .py files in src/ferrite/ for raw MATCH usage
outside of sanctioned query-building modules (temporal.py, query.py,
ingestion.py, consolidator.py, mental_models.py, canonicalize.py,
vector_store.py, mcp_server.py, eval.py).

Allowed modules are where Cypher is actually constructed — those are
the single point where namespace injection happens. Handler modules
(api.py, quality_gates.py, key_store.py, etc.) must never construct
raw Cypher.
"""

import re
import sys
from pathlib import Path


def test_no_raw_match_in_handler_modules():
    """Handler modules must not contain raw MATCH Cypher strings.

    All Cypher queries must go through the query builder modules
    (query.py, temporal.py, etc.) that inject namespace predicates.
    """
    # Modules where Cypher construction is ALLOWED (query builders + monitoring)
    allowed_modules = {
        "temporal.py",
        "query.py",
        "ingestion.py",
        "consolidator.py",
        "mental_models.py",
        "canonicalize.py",
        "vector_store.py",
        "mcp_server.py",
        "eval.py",
        "schema.py",
        "observability.py",  # read-only health monitoring
    }

    src_dir = Path(__file__).parent.parent / "src" / "ferrite"
    if not src_dir.exists():
        print(f"Skipping — src dir not found at {src_dir}")
        return

    violations: list[str] = []

    for py_file in src_dir.glob("*.py"):
        if py_file.name in allowed_modules:
            continue

        content = py_file.read_text()
        # Look for raw MATCH (Cypher) in string literals
        # Match "MATCH (..." or 'MATCH (...' in triple-quoted or regular strings
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue
            # Skip docstrings (lines between triple quotes)
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            # Look for raw MATCH in string literals
            if re.search(r'["\']MATCH\s*\(', stripped, re.IGNORECASE):
                violations.append(
                    f"{py_file.name}:{i}: {stripped[:100]}"
                )

    if violations:
        violation_text = "\n".join(violations)
        raise AssertionError(
            f"Raw MATCH Cypher found in handler modules (§6.3 violation).\n"
            f"All Cypher must go through query builder modules "
            f"(query.py, temporal.py, etc.) that inject namespace predicates.\n"
            f"Violations:\n{violation_text}"
        )


def test_namespace_predicate_in_query_builder():
    """Query builder modules must include namespace filtering logic."""
    src_dir = Path(__file__).parent.parent / "src" / "ferrite"

    # Check that query.py has namespace filtering
    query_py = src_dir / "query.py"
    if query_py.exists():
        content = query_py.read_text()
        assert "namespace" in content.lower(), (
            "query.py must contain namespace filtering logic (§6.3)"
        )

    # Check that temporal.py has namespace filtering
    temporal_py = src_dir / "temporal.py"
    if temporal_py.exists():
        content = temporal_py.read_text()
        assert "namespace" in content.lower(), (
            "temporal.py must contain namespace filtering logic (§6.3)"
        )


if __name__ == "__main__":
    try:
        test_no_raw_match_in_handler_modules()
        print("✓ No raw MATCH in handler modules")
    except AssertionError as e:
        print(f"✗ {e}")
        sys.exit(1)

    try:
        test_namespace_predicate_in_query_builder()
        print("✓ Namespace filtering in query builders")
    except AssertionError as e:
        print(f"✗ {e}")
        sys.exit(1)

    print("All namespace CI tests passed.")
