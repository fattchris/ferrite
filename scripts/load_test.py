#!/usr/bin/env python3
"""Load test for Ferrite API (P-4 fix).

Tests:
1. Search throughput (GET /search)
2. Ingestion throughput (POST /store)
3. Mixed read/write workload
4. Rate limit verification (429 response)

Usage:
    python scripts/load_test.py [--base-url http://localhost:8001] [--duration 30]
"""

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_URL = "http://localhost:8001"
API_KEY = ""

def make_request(method: str, path: str, body: dict | None = None) -> tuple[int, float]:
    """Make HTTP request, return (status_code, latency_ms)."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if API_KEY:
        req.add_header("Authorization", f"Bearer {API_KEY}")
    start = time.time()
    try:
        resp = urlopen(req, timeout=10)
        latency = (time.time() - start) * 1000
        return resp.status, latency
    except HTTPError as e:
        latency = (time.time() - start) * 1000
        return e.code, latency
    except Exception:
        latency = (time.time() - start) * 1000
        return 0, latency


def test_search_throughput(duration_s: int) -> dict:
    """Hammer /search for N seconds, report throughput + latencies."""
    print(f"\n[1] Search throughput ({duration_s}s)...")
    latencies: list[float] = []
    errors = 0
    rate_limited = 0
    end_time = time.time() + duration_s
    count = 0

    queries = ["GLM", "Spark", "cluster", "DGX", "inference", "model", "GPU"]

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        while time.time() < end_time:
            q = queries[count % len(queries)]
            f = executor.submit(make_request, "GET", f"/search?query={q}&limit=5")
            futures.append(f)
            count += 1
            if len(futures) >= 100:
                break

        for f in as_completed(futures):
            status, latency = f.result()
            if status == 200:
                latencies.append(latency)
            elif status == 429:
                rate_limited += 1
            else:
                errors += 1

    total = len(latencies) + errors + rate_limited
    rps = total / duration_s if duration_s > 0 else 0
    p50 = statistics.median(latencies) if latencies else 0
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else 0
    p99 = (
        statistics.quantiles(latencies, n=100)[98]
        if len(latencies) >= 100
        else max(latencies) if latencies else 0
    )

    result = {
        "total_requests": total,
        "successful": len(latencies),
        "errors": errors,
        "rate_limited": rate_limited,
        "rps": round(rps, 1),
        "latency_p50_ms": round(p50, 1),
        "latency_p95_ms": round(p95, 1),
        "latency_p99_ms": round(p99, 1),
    }
    print(f"  RPS: {result['rps']}, p50: {result['latency_p50_ms']}ms, "
          f"p95: {result['latency_p95_ms']}ms, errors: {errors}")
    return result


def test_ingestion_throughput(duration_s: int) -> dict:
    """Hammer /store for N seconds."""
    print(f"\n[2] Ingestion throughput ({duration_s}s)...")
    latencies: list[float] = []
    errors = 0
    rate_limited = 0
    end_time = time.time() + duration_s
    count = 0

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        while time.time() < end_time:
            body = {
                "content": (
                    f"Load test episode {count}: "
                    f"The DGX Spark cluster runs GLM-5.2 "
                    f"inference on node {count % 8}."
                ),
                "content_type": "text/plain",
                "source": {"channel": "load-test"},
                "namespace": "e2e-test",
            }
            f = executor.submit(make_request, "POST", "/store", body)
            futures.append(f)
            count += 1
            if len(futures) >= 50:
                break

        for f in as_completed(futures):
            status, latency = f.result()
            if status == 200:
                latencies.append(latency)
            elif status == 429:
                rate_limited += 1
            else:
                errors += 1

    total = len(latencies) + errors + rate_limited
    rps = total / duration_s if duration_s > 0 else 0
    p50 = statistics.median(latencies) if latencies else 0
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else 0

    result = {
        "total_requests": total,
        "successful": len(latencies),
        "errors": errors,
        "rate_limited": rate_limited,
        "rps": round(rps, 1),
        "latency_p50_ms": round(p50, 1),
        "latency_p95_ms": round(p95, 1),
    }
    print(f"  RPS: {result['rps']}, p50: {result['latency_p50_ms']}ms, "
          f"rate_limited: {rate_limited}")
    return result


def test_mixed_workload(duration_s: int) -> dict:
    """80% reads / 20% writes for N seconds."""
    print(f"\n[3] Mixed workload 80/20 ({duration_s}s)...")
    latencies: list[float] = []
    errors = 0
    rate_limited = 0
    end_time = time.time() + duration_s
    count = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        while time.time() < end_time:
            if count % 5 == 0:
                body = {
                    "content": (
                        f"Mixed test episode {count}: "
                        f"Spark node {count % 8} health check passed."
                    ),
                    "content_type": "text/plain",
                    "source": {"channel": "mixed-test"},
                    "namespace": "e2e-test",
                }
                f = executor.submit(make_request, "POST", "/store", body)
            else:
                f = executor.submit(
                    make_request, "GET", "/search?query=Spark&limit=3"
                )
            futures.append(f)
            count += 1
            if len(futures) >= 80:
                break

        for f in as_completed(futures):
            status, latency = f.result()
            if status in (200, 201):
                latencies.append(latency)
            elif status == 429:
                rate_limited += 1
            else:
                errors += 1

    total = len(latencies) + errors + rate_limited
    rps = total / duration_s if duration_s > 0 else 0
    p50 = statistics.median(latencies) if latencies else 0

    result = {
        "total_requests": total,
        "successful": len(latencies),
        "errors": errors,
        "rate_limited": rate_limited,
        "rps": round(rps, 1),
        "latency_p50_ms": round(p50, 1),
    }
    print(
        f"  RPS: {result['rps']}, p50: {result['latency_p50_ms']}ms, "
        f"errors: {errors}, rate_limited: {rate_limited}"
    )
    return result


def test_rate_limiting() -> dict:
    """Fire 150 rapid requests, verify 429 responses appear."""
    print("\n[4] Rate limit verification...")
    rate_limited = 0
    ok = 0
    other = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [
            executor.submit(make_request, "GET", "/search?query=test&limit=1")
            for _ in range(150)
        ]
        for f in as_completed(futures):
            status, _ = f.result()
            if status == 200:
                ok += 1
            elif status == 429:
                rate_limited += 1
            else:
                other += 1

    print(f"  200 OK: {ok}, 429 Rate Limited: {rate_limited}, Other: {other}")
    return {"ok": ok, "rate_limited": rate_limited, "other": other}


def main():
    global BASE_URL, API_KEY
    parser = argparse.ArgumentParser(description="Ferrite Load Test")
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--duration", type=int, default=10)
    args = parser.parse_args()
    BASE_URL = args.base_url
    API_KEY = args.api_key

    print("=== Ferrite Load Test ===")
    print(f"Target: {BASE_URL}")
    print(f"Duration: {args.duration}s per test")

    # Health check first
    status, _ = make_request("GET", "/health")
    if status != 200:
        print(f"❌ Health check failed: {status}")
        sys.exit(1)
    print("✅ Health check passed")

    results = {}
    results["search"] = test_search_throughput(args.duration)
    results["ingestion"] = test_ingestion_throughput(args.duration)
    results["mixed"] = test_mixed_workload(args.duration)
    results["rate_limit"] = test_rate_limiting()

    print("\n" + "=" * 50)
    print("LOAD TEST SUMMARY")
    print("=" * 50)
    for test, data in results.items():
        print(f"\n{test}:")
        for k, v in data.items():
            print(f"  {k}: {v}")

    # Pass/fail criteria
    passed = True
    if results["search"]["errors"] > results["search"]["total_requests"] * 0.1:
        print("\n❌ FAIL: Search error rate > 10%")
        passed = False
    if results["mixed"]["errors"] > results["mixed"]["total_requests"] * 0.1:
        print("\n❌ FAIL: Mixed workload error rate > 10%")
        passed = False
    if results["search"]["latency_p95_ms"] > 500:
        print("\n❌ FAIL: Search p95 latency > 500ms")
        passed = False
    if results["rate_limit"]["rate_limited"] == 0:
        print("\n❌ FAIL: Rate limiting not enforced")
        passed = False

    if passed:
        print("\n✅ ALL LOAD TESTS PASSED")
        sys.exit(0)
    else:
        print("\n❌ LOAD TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
