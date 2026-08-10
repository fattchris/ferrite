#!/usr/bin/env python3
"""Local CI pipeline runner for Ferrite (P-7 fix).

Runs all gates in sequence:
1. Lint (ruff)
2. Unit tests
3. Namespace CI test
4. E2E tests (requires running services)
5. Recall gate (recall@5 >= 0.30)
6. Load test (optional)

Usage:
    python scripts/ci_pipeline.py [--skip-load-test]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# Default location of the .env file shipped with the project.
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_env() -> None:
    """Populate os.environ from ~/ferrite/.env for any keys not already set.

    This is a minimal .env loader: it only sets variables that are not already
    present in the environment, so real environment variables always take
    precedence over the values defined in the file.
    """
    if not _ENV_PATH.is_file():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _get_api_key() -> str | None:
    """Return FERRITE_API_KEY from the environment, falling back to .env."""
    key = os.environ.get("FERRITE_API_KEY")
    if key:
        return key
    load_env()
    return os.environ.get("FERRITE_API_KEY")


def run(cmd: str, name: str) -> bool:
    """Run a command, return True if exit 0."""
    print(f"\n{'='*50}")
    print(f"  {name}")
    print(f"{'='*50}")
    result = subprocess.run(cmd, shell=True, capture_output=False)
    if result.returncode != 0:
        print(f"❌ FAIL: {name}")
        return False
    print(f"✅ PASS: {name}")
    return True


def recall_gate() -> bool:
    """Run eval and check recall@5 >= 0.30."""
    print(f"\n{'='*50}")
    print("  Recall Gate (recall@5 >= 0.30)")
    print(f"{'='*50}")
    try:
        api_key = _get_api_key()
        if not api_key:
            print("❌ FAIL: FERRITE_API_KEY not set and not found in .env")
            return False
        req = Request(
            "http://localhost:8001/eval",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp = urlopen(req, timeout=60)
        data = json.loads(resp.read())
        if "error" in data:
            print(f"❌ FAIL: Eval error: {data['error']}")
            return False
        recall5 = data.get("recall", {}).get("recall@5", 0)
        recall10 = data.get("recall", {}).get("recall@10", 0)
        mrr = data.get("mrr", 0)
        print(f"  recall@5:  {recall5}")
        print(f"  recall@10: {recall10}")
        print(f"  MRR:       {mrr}")
        if recall5 < 0.30:
            print(f"❌ FAIL: recall@5 = {recall5} < 0.30")
            return False
        print(f"✅ PASS: recall@5 = {recall5} >= 0.30")
        return True
    except URLError as e:
        print(f"❌ FAIL: Cannot reach API: {e}")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-load-test", action="store_true")
    args = parser.parse_args()

    gates = []

    # Gate 1: Lint
    gates.append(("Lint", run("cd ~/ferrite && uv run ruff check src/ tests/", "Lint (ruff)")))

    # Gate 2: Unit tests
    gates.append(("Unit Tests", run("cd ~/ferrite && uv run pytest tests/ -q", "Unit Tests")))

    # Gate 3: Namespace CI
    gates.append((
        "Namespace CI",
        run("cd ~/ferrite && uv run pytest tests/test_namespace_ci.py -v", "Namespace CI")
    ))

    # Gate 4: E2E
    cmd = "cd ~/ferrite && uv run python scripts/e2e_test.py"
    gates.append(("E2E Tests", run(cmd, "E2E Tests")))

    # Gate 5: Recall gate
    gates.append(("Recall Gate", recall_gate()))

    # Gate 6: Load test (optional)
    if not args.skip_load_test:
        cmd = "cd ~/ferrite && uv run python scripts/load_test.py --duration 5"
        gates.append(("Load Test", run(cmd, "Load Test")))

    # Summary
    print(f"\n{'='*50}")
    print("CI PIPELINE SUMMARY")
    print(f"{'='*50}")
    all_passed = True
    for name, passed in gates:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\n✅ ALL GATES PASSED — MERGE APPROVED")
        sys.exit(0)
    else:
        print("\n❌ GATES FAILED — MERGE BLOCKED")
        sys.exit(1)


if __name__ == "__main__":
    main()
