"""Local pytest replica of the CI `check_raw_match` grep (§6.3).

The CI workflow (``.github/workflows/ci.yml`` -> ``check_raw_match`` job)
greps ``src/ferrite/*.py`` for raw ``MATCH`` Cypher strings inlined in
*handler* modules. Per §6.3 all Cypher must go through the query builder
(``query.py`` + sanctioned builder modules) that injects the namespace
predicate on every Fact node pattern; handler modules (``api.py``,
``quality_gates.py``, ``key_store.py``, …) must never construct raw Cypher.

This test mirrors that grep locally so the audit (``scripts/audit_build.py``)
can detect it via the test suite, and so the check runs under ``pytest -k grep``
on a developer machine without GitHub Actions.

Detection mirrors the established ``test_namespace_ci.py`` semantics: it flags
``MATCH (`` appearing inside a string literal (``"MATCH (`` or ``'MATCH (``).
Multi-line triple-quoted queries where ``MATCH`` sits on its own line are
governed by ``test_namespace_ci.py``; this test focuses on the inline raw-MATCH
form the CI grep targets.
"""

import re
import sys
from pathlib import Path

# Modules where Cypher construction is sanctioned (the single point where
# namespace injection happens). Matches the exclusion list in ci.yml and the
# allowed set in test_namespace_ci.py.
ALLOWED_MODULES = {
    "query.py",
    "temporal.py",
    "ingestion.py",
    "consolidator.py",
    "mental_models.py",
    "canonicalize.py",
    "vector_store.py",
    "mcp_server.py",
    "eval.py",
    "schema.py",
    "observability.py",
    "tempr.py",
}

# Inline raw MATCH in a string literal, e.g.  session.run("MATCH (e:Entity) …")
RAW_MATCH_RE = re.compile(r"""['"]MATCH\s*\(""", re.IGNORECASE)

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "ferrite"


def test_grep_no_raw_match_in_handlers():
    """Grep replica: handler modules must not inline raw MATCH Cypher.

    If this fails, a handler module is constructing Cypher directly instead of
    routing through the query builder (query.py) that injects namespace
    filters — a §6.3 violation. Move the query into a builder module and call
    it from the handler.
    """
    if not SRC_DIR.exists():
        print(f"Skipping — src dir not found at {SRC_DIR}")
        return

    violations: list[str] = []

    for py_file in sorted(SRC_DIR.glob("*.py")):
        if py_file.name in ALLOWED_MODULES:
            continue

        content = py_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            # Skip comment lines.
            if stripped.startswith("#"):
                continue
            # Skip docstring / multi-line-string opening lines.
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if RAW_MATCH_RE.search(stripped):
                violations.append(f"{py_file.name}:{i}: {stripped[:120]}")

    if violations:
        violation_text = "\n".join(violations)
        raise AssertionError(
            f"Raw MATCH Cypher found in handler modules (§6.3 violation).\n"
            f"All Cypher must go through query builder modules (query.py, "
            f"temporal.py, …) that inject namespace predicates.\n"
            f"Violations:\n{violation_text}"
        )


def test_ci_workflow_has_check_raw_match_step():
    """The CI workflow must define the check_raw_match step with a MATCH grep."""
    ci_path = SRC_DIR.parent.parent / ".github" / "workflows" / "ci.yml"
    if not ci_path.exists():
        print(f"Skipping — CI workflow not found at {ci_path}")
        return
    ci_text = ci_path.read_text()
    assert "check_raw_match" in ci_text, (
        "ci.yml must define a `check_raw_match` step (§6.3 / GAP-004)"
    )
    assert "MATCH" in ci_text, (
        "ci.yml check_raw_match step must grep for MATCH (§6.3 / GAP-004)"
    )


if __name__ == "__main__":
    try:
        test_grep_no_raw_match_in_handlers()
        print("✓ grep: no raw MATCH in handler modules")
    except AssertionError as e:
        print(f"✗ {e}")
        sys.exit(1)

    try:
        test_ci_workflow_has_check_raw_match_step()
        print("✓ grep: CI workflow defines check_raw_match")
    except AssertionError as e:
        print(f"✗ {e}")
        sys.exit(1)

    print("All grep match-ban tests passed.")
