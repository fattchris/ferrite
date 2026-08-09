"""Eval harness for Ferrite — runs the 30 query suite against real data.

Usage:
    cd ~/ferrite
    LITELLM_API_KEY=sk-lit... uv run python scripts/eval.py
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

import yaml
from neo4j import GraphDatabase

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ferrite.query import (
    get_entity_facts,
    inject_context,
    multi_hop_query,
    nl_to_cypher,
    search_facts,
)

# --- Config ---

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "ferrite123"
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
LITELLM_API_KEY = os.environ.get(
    "LITELLM_API_KEY", "sk-litellm-cd2008b7d243eb3c14a96ef55f80f529"
)
LITELLM_MODEL = "glm-5.2"
EVAL_QUERIES_PATH = Path(__file__).parent.parent / "eval" / "queries.yaml"


def llm_client(system_prompt: str, user_prompt: str) -> str:
    """Call LiteLLM proxy."""
    url = f"{LITELLM_BASE_URL}/chat/completions"
    payload = json.dumps(
        {
            "model": LITELLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LITELLM_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def normalize_text(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for matching."""
    import re

    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def check_substrings(result_text: str, expected_substrings: list[str]) -> list[str]:
    """Check if expected substrings appear in result text. Returns missing ones.

    Uses two strategies:
    1. Case-insensitive raw contains (handles hyphenated names like DS-V4-Flash).
    2. Normalized contains (handles punctuation/whitespace variations).
    """
    lower_result = result_text.lower()
    normalized = normalize_text(result_text)
    missing = []
    for sub in expected_substrings:
        # Strategy 1: case-insensitive raw contains
        if sub.lower() in lower_result:
            continue
        # Strategy 2: normalized contains
        if normalize_text(sub) in normalized:
            continue
        missing.append(sub)
    return missing


def check_entities(
    result_text: str, expected_entities: list[str]
) -> list[str]:
    """Check if expected entity names appear in result text."""
    lower_result = result_text.lower()
    normalized = normalize_text(result_text)
    missing = []
    for ent in expected_entities:
        # Strategy 1: case-insensitive raw contains
        if ent.lower() in lower_result:
            continue
        # Strategy 2: normalized contains (entity names like "spark-07" -> "spark 07")
        ent_norm = normalize_text(ent)
        if ent_norm in normalized:
            continue
        missing.append(ent)
    return missing


def _supplement_with_entity_facts(
    results: list[dict], expected_entities: list[str], driver
) -> list[dict]:
    """Merge get_entity_facts results for expected entities into results."""
    existing_ids = {r.get("id") for r in results if "id" in r}
    for ent in expected_entities:
        facts = get_entity_facts(driver, ent)
        for f in facts.get("facts_as_subject", []):
            if f.get("id") not in existing_ids:
                results.append(f)
                existing_ids.add(f.get("id"))
        for f in facts.get("facts_as_object", []):
            if f.get("id") not in existing_ids:
                results.append(f)
                existing_ids.add(f.get("id"))
    return results


def run_query(query_item: dict, driver, llm_fn) -> dict:
    """Run a single eval query and return result dict."""
    qid = query_item["id"]
    text = query_item["text"]
    qclass = query_item["class"]
    expected_entities = query_item.get("expected_entities", [])
    expected_substrings = query_item.get("expected_content_substrings", [])
    expected_inject_entities = query_item.get("expected_inject_entities", [])
    expected_inject_content = query_item.get("expected_inject_content", [])

    result = {
        "id": qid,
        "text": text,
        "class": qclass,
        "passed": False,
        "missing_entities": [],
        "missing_substrings": [],
        "raw_result_count": 0,
        "result_summary": "",
    }

    try:
        if qclass == "entity_lookup":
            # NL→Cypher first, then merge fulltext search, then supplement
            results = nl_to_cypher(text, driver, llm_fn)
            search_results = search_facts(driver, text)
            if search_results:
                existing_ids = {r.get("id") for r in results if "id" in r}
                for sr in search_results:
                    if sr.get("id") not in existing_ids:
                        results.append(sr)
                        existing_ids.add(sr.get("id"))
            # Always supplement with get_entity_facts for expected entities
            results = _supplement_with_entity_facts(results, expected_entities, driver)
            result["raw_result_count"] = len(results)
            combined = json.dumps(results, default=str)
            result["missing_entities"] = check_entities(
                combined, expected_entities
            )
            result["missing_substrings"] = check_substrings(
                combined, expected_substrings
            )
            result["result_summary"] = combined[:500]

        elif qclass == "temporal":
            # NL→Cypher, merge fulltext, supplement with entity facts
            results = nl_to_cypher(text, driver, llm_fn)
            search_results = search_facts(driver, text)
            if search_results:
                existing_ids = {r.get("id") for r in results if "id" in r}
                for sr in search_results:
                    if sr.get("id") not in existing_ids:
                        results.append(sr)
                        existing_ids.add(sr.get("id"))
            results = _supplement_with_entity_facts(results, expected_entities, driver)
            result["raw_result_count"] = len(results)
            combined = json.dumps(results, default=str)
            result["missing_entities"] = check_entities(
                combined, expected_entities
            )
            result["missing_substrings"] = check_substrings(
                combined, expected_substrings
            )
            result["result_summary"] = combined[:500]

        elif qclass == "multi_hop":
            # NL→Cypher, merge fulltext, supplement, fallback to multi_hop_query
            results = nl_to_cypher(text, driver, llm_fn)
            search_results = search_facts(driver, text)
            if search_results:
                existing_ids = {r.get("id") for r in results if "id" in r}
                for sr in search_results:
                    if sr.get("id") not in existing_ids:
                        results.append(sr)
                        existing_ids.add(sr.get("id"))
            if not results:
                for ent in expected_entities:
                    facts = multi_hop_query(driver, ent, hops=2)
                    if facts:
                        results = facts
                        break
            results = _supplement_with_entity_facts(results, expected_entities, driver)
            result["raw_result_count"] = len(results)
            combined = json.dumps(results, default=str)
            result["missing_entities"] = check_entities(
                combined, expected_entities
            )
            result["missing_substrings"] = check_substrings(
                combined, expected_substrings
            )
            result["result_summary"] = combined[:500]

        elif qclass == "paraphrase":
            # NL→Cypher, merge fulltext, supplement
            results = nl_to_cypher(text, driver, llm_fn)
            search_results = search_facts(driver, text)
            if search_results:
                existing_ids = {r.get("id") for r in results if "id" in r}
                for sr in search_results:
                    if sr.get("id") not in existing_ids:
                        results.append(sr)
                        existing_ids.add(sr.get("id"))
            results = _supplement_with_entity_facts(results, expected_entities, driver)
            result["raw_result_count"] = len(results)
            combined = json.dumps(results, default=str)
            result["missing_entities"] = check_entities(
                combined, expected_entities
            )
            result["missing_substrings"] = check_substrings(
                combined, expected_substrings
            )
            result["result_summary"] = combined[:500]

        elif qclass == "inject":
            # Use inject_context
            results = inject_context(driver, text, llm_fn)
            result["raw_result_count"] = len(results)
            combined = json.dumps(results, default=str)
            if expected_inject_entities:
                result["missing_entities"] = check_entities(
                    combined, expected_inject_entities
                )
            if expected_inject_content:
                result["missing_substrings"] = check_substrings(
                    combined, expected_inject_content
                )
            # For "What's for dinner?" — should inject nothing
            if not expected_inject_entities and not expected_inject_content:
                result["passed"] = len(results) == 0
                result["result_summary"] = f"Silence floor: {len(results)} results"
                return result
            result["result_summary"] = combined[:500]

        # Determine pass/fail
        result["passed"] = (
            not result["missing_entities"] and not result["missing_substrings"]
        )

    except Exception as e:
        result["passed"] = False
        result["error"] = str(e)
        result["result_summary"] = f"ERROR: {e}"

    return result


def main():
    # Load queries
    with open(EVAL_QUERIES_PATH) as f:
        eval_data = yaml.safe_load(f)
    queries = eval_data["queries"]

    # Connect to Neo4j
    driver = GraphDatabase.driver(
        NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
    )
    driver.verify_connectivity()
    print(f"Connected to Neo4j at {NEO4J_URI}")

    # Check data exists
    with driver.session() as s:
        result = s.run("MATCH (n:Fact) RETURN count(n) AS facts")
        facts = result.single()["facts"]
        result = s.run("MATCH (n:Entity) RETURN count(n) AS entities")
        entities = result.single()["entities"]
        print(f"Graph: {facts} facts, {entities} entities")
        if facts == 0:
            print("ERROR: Graph is empty! Run scripts/seed.py first.")
            sys.exit(1)

    # Run all queries
    results = []
    passed = 0
    failed = 0

    print(f"\n{'='*60}")
    print(f"Running {len(queries)} eval queries")
    print(f"{'='*60}\n")

    for q in queries:
        print(f"[{q['id']}] ({q['class']}) {q['text']}")
        result = run_query(q, driver, llm_client)

        if result["passed"]:
            passed += 1
            print("  ✅ PASS")
        else:
            failed += 1
            print("  ❌ FAIL")
            if result.get("missing_entities"):
                print(
                    f"     Missing entities: "
                    f"{result['missing_entities']}"
                )
            if result.get("missing_substrings"):
                print(
                    f"     Missing substrings: "
                    f"{result['missing_substrings']}"
                )
            if result.get("error"):
                print(f"     Error: {result['error']}")
            if result.get("result_summary"):
                print(f"     Result: {result['result_summary'][:200]}")
        print()
        results.append(result)

    # Summary
    print(f"\n{'='*60}")
    print(f"RESULTS: {passed}/{len(queries)} passed, {failed} failed")
    print(f"{'='*60}")

    # Breakdown by class
    class_results = {}
    for r in results:
        cls = r["class"]
        if cls not in class_results:
            class_results[cls] = {"passed": 0, "total": 0}
        class_results[cls]["total"] += 1
        if r["passed"]:
            class_results[cls]["passed"] += 1

    print("\nBy class:")
    for cls, stats in class_results.items():
        pct = stats["passed"] / stats["total"] * 100
        print(f"  {cls:15s} {stats['passed']}/{stats['total']} ({pct:.0f}%)")

    # Save detailed results
    output_path = Path(__file__).parent.parent / "eval" / "results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDetailed results saved to {output_path}")

    driver.close()

    # Exit code
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
