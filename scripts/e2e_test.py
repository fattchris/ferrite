#!/usr/bin/env python
# ruff: noqa: E501
"""E2E test: seed data + exercise every API endpoint against live Docker stack."""

import json
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://localhost:8001"
API_KEY = "test-secret"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

passed = 0
failed = 0


def call(method, path, data=None, auth=True):
    headers = dict(HEADERS) if auth else {"Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None
    req = Request(f"{BASE}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(req) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw.decode()}
    except HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode()}


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")


def test_endpoint(name, method, path, expected_status, **kwargs):
    status, body = call(method, path, **kwargs)
    test(name, status == expected_status, f"got {status}: {body}")


def test_auth_rejected(name, method, path):
    """Test that endpoints without auth are rejected."""
    status, body = call(method, path, auth=False)
    test(name, status == 401, f"got {status}: {body}")


def seed_test_data():
    """Seed a few entities + facts directly into Neo4j."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        "bolt://localhost:7687", auth=("neo4j", "ferrite123")
    )
    with driver.session() as s:
        # Clean test namespace
        s.run("MATCH (n) WHERE n.namespace = 'e2e-test' DETACH DELETE n")

        # Create entities
        s.run("""
            CREATE (e1:Entity {id: 'e2e-spark', type: 'Device', name: 'DGX Spark', summary: 'NVIDIA DGX Spark GB10 node', namespace: 'e2e-test'})
            CREATE (e2:Entity {id: 'e2e-glm', type: 'Model', name: 'GLM-5.2', summary: 'Large language model', namespace: 'e2e-test'})
            CREATE (e3:Entity {id: 'e2e-cluster', type: 'Cluster', name: 'Spark Cluster', summary: '8-node DGX Spark cluster', namespace: 'e2e-test'})
        """)

        # Create facts
        s.run("""
            CREATE (f1:Fact {
                id: 'e2e-f1', statement: 'GLM-5.2 runs on DGX Spark cluster with TP4',
                predicate: 'runs_on', certainty: 0.95,
                epistemic_state: 'asserted', valid_at: '2026-08-01',
                recorded_at: '2026-08-01T00:00:00Z',
                namespace: 'e2e-test', assertion_source: 'e2e-test'
            })
            CREATE (f2:Fact {
                id: 'e2e-f2', statement: 'DGX Spark has 128GB unified memory',
                predicate: 'has_spec', certainty: 0.99,
                epistemic_state: 'asserted', valid_at: '2026-08-01',
                recorded_at: '2026-08-01T00:00:00Z',
                namespace: 'e2e-test', assertion_source: 'e2e-test'
            })
            CREATE (f3:Fact {
                id: 'e2e-f3', statement: 'GLM-5.2 TP4 MTP4 uses port 8210',
                predicate: 'configured_as', certainty: 0.90,
                epistemic_state: 'asserted', valid_at: '2026-08-01',
                recorded_at: '2026-08-01T00:00:00Z',
                namespace: 'e2e-test', assertion_source: 'e2e-test'
            })
        """)

        # Link facts to entities
        s.run("""
            MATCH (f1:Fact {id: 'e2e-f1'}), (e1:Entity {id: 'e2e-glm'}), (e2:Entity {id: 'e2e-spark'})
            CREATE (f1)-[:SUBJECT]->(e1), (f1)-[:OBJECT]->(e2)
        """)
        s.run("""
            MATCH (f2:Fact {id: 'e2e-f2'}), (e1:Entity {id: 'e2e-spark'})
            CREATE (f2)-[:SUBJECT]->(e1), (f2)-[:OBJECT]->(e1)
        """)
        s.run("""
            MATCH (f3:Fact {id: 'e2e-f3'}), (e1:Entity {id: 'e2e-glm'})
            CREATE (f3)-[:SUBJECT]->(e1), (f3)-[:OBJECT]->(e1)
        """)

    driver.close()
    print("  ✅ Seeded 3 entities + 3 facts in e2e-test namespace")


def main():
    global passed, failed

    print("\n=== E2E Test Suite ===\n")

    # --- 0. Seed data ---
    print("[0] Seeding test data...")
    try:
        seed_test_data()
        passed += 1
    except Exception as e:
        failed += 1
        print(f"  ❌ Seed failed: {e}")
        print("  ⏭️  Continuing with whatever data exists...")

    time.sleep(1)  # Let indexes settle

    # --- 1. Health (public, no auth) ---
    print("\n[1] Health endpoint (public)...")
    status, body = call("GET", "/health", auth=False)
    test("GET /health returns 200", status == 200, f"got {status}")
    test("Health has neo4j=ok", body.get("neo4j") == "ok", body)
    test("Health has redis=ok", body.get("redis") == "ok", body)

    # --- 2. Auth tests ---
    print("\n[2] Auth tests...")
    test_auth_rejected("GET /search without auth → 401", "GET", "/search?query=test")
    test_auth_rejected("POST /store without auth → 401", "POST", "/store")
    test_auth_rejected("GET /entities/e2e-spark without auth → 401", "GET", "/entities/e2e-spark")
    test_auth_rejected("POST /tempr without auth → 401", "POST", "/tempr")

    # --- 3. Search ---
    print("\n[3] Search endpoint...")
    status, body = call("GET", "/search?query=GLM+Spark")
    test("GET /search returns 200", status == 200, f"got {status}: {body}")
    results = body.get("results", [])
    test("Search finds results", len(results) > 0, f"got {len(results)} results")
    if results:
        test(
            "Search result has statement",
            "statement" in results[0],
            results[0],
        )

    # --- 4. Entity ---
    print("\n[4] Entity endpoint...")
    status, body = call("GET", "/entities/e2e-spark")
    test("GET /entities/{id} returns 200", status == 200, f"got {status}: {body}")
    if status == 200:
        test("Entity has id", body.get("entity", {}).get("id") == "e2e-spark", body)
        test(
            "Entity has facts_as_subject",
            isinstance(body.get("facts_as_subject"), list),
            body,
        )

    # --- 5. History ---
    print("\n[5] History endpoint...")
    status, body = call("GET", "/history/e2e-spark?mode=knowledge")
    test("GET /history returns 200", status == 200, f"got {status}: {body}")
    if status == 200:
        test("History has facts", isinstance(body.get("facts"), list), body)

    # --- 6. Metrics ---
    print("\n[6] Metrics endpoint...")
    status, body = call("GET", "/metrics")
    test("GET /metrics returns 200", status == 200, f"got {status}")
    if status == 200:
        test("Metrics has health", "health" in body, body)
        test(
            "Health overall=healthy",
            body.get("health", {}).get("overall") == "healthy",
            body.get("health", {}),
        )

    # --- 7. Circuit Breaker ---
    print("\n[7] Circuit breaker endpoint...")
    status, body = call("GET", "/circuit-breaker")
    test("GET /circuit-breaker returns 200", status == 200, f"got {status}")
    if status == 200:
        test("Breaker state=closed", body.get("state") == "closed", body)
        test(
            "Breaker has failure_threshold",
            "failure_threshold" in body,
            body,
        )

    # --- 8. TEMPR ---
    print("\n[8] TEMPR endpoint...")
    status, body = call(
        "POST", "/tempr", data={"query": "GLM Spark cluster", "limit": 5}
    )
    test("POST /tempr returns 200", status == 200, f"got {status}: {body}")
    if status == 200:
        test(
            "TEMPR returns results",
            isinstance(body.get("results"), list),
            body,
        )
        test(
            "TEMPR result count matches",
            body.get("count") == len(body.get("results", [])),
            body,
        )

    # --- 9. Mental Models ---
    print("\n[9] Mental models endpoint...")
    status, body = call("GET", "/mental-models?query=spark")
    test("GET /mental-models returns 200", status == 200, f"got {status}: {body}")
    if status == 200:
        test(
            "Mental models returns list",
            isinstance(body.get("results"), list),
            body,
        )

    # --- 10. Consolidate ---
    print("\n[10] Consolidate endpoint...")
    status, body = call("POST", "/consolidate")
    test("POST /consolidate returns 200", status == 200, f"got {status}: {body}")
    if status == 200:
        test(
            "Consolidate returns count",
            "consolidated_groups" in body,
            body,
        )

    # --- 11. Eval ---
    print("\n[11] Eval endpoint...")
    status, body = call("GET", "/eval")
    test("GET /eval returns 200", status == 200, f"got {status}: {body}")
    if status == 200 and isinstance(body, dict):
        test("Eval has recall", "recall" in body, body)
        test("Eval has mrr", "mrr" in body, body)

    # --- 12. Store (ingestion) ---
    print("\n[12] Store endpoint...")
    status, body = call(
        "POST",
        "/store",
        data={
            "content": "New test fact: GLM-5.2 supports MTP4 multi-token prediction",
            "content_type": "text",
            "source": {"channel": "e2e-test"},
            "namespace": "e2e-test",
        },
    )
    test("POST /store returns 200", status == 200, f"got {status}: {body}")
    if status == 200:
        test("Store returns episode_id", "episode_id" in body, body)
        test("Store status=queued", body.get("status") == "queued", body)

    # --- 13. Circuit breaker reset ---
    print("\n[13] Circuit breaker reset...")
    status, body = call("POST", "/circuit-breaker/reset")
    test("POST /circuit-breaker/reset returns 200", status == 200, f"got {status}")
    if status == 200:
        test("Reset returns state=closed", body.get("state") == "closed", body)

    # --- 14. 404 test ---
    print("\n[14] 404 test...")
    status, body = call("GET", "/entities/nonexistent")
    test("GET /entities/nonexistent returns 404", status == 404, f"got {status}")

    # --- Summary ---
    total = passed + failed
    print(f"\n{'='*50}")
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")
    print(f"{'='*50}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
