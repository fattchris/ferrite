# Adversarial Architecture Review — Ferrite V3 Implementation
**Reviewer:** Red Team (Adversarial)
**Verdict:** REJECT
**Score:** 6/10
**Date:** 2026-08-09

## Summary

The V3 implementation closes all 10 spec gaps from the prior audit and
achieves 49/49 spec-item coverage. That's genuine progress — the codebase
has real key management, a real LRU, a real async consumer, and real
quality gates. The test suite (176 unit + 38 E2E) is non-trivial.

However, the implementation has two FATAL access-control bypasses that
make it unsafe for multi-agent production use, and four SERIOUS issues
that will cause silent data loss or unbounded latency. The "rate
limiting" middleware function name is a lie — it does auth only.

The core problem is that spec-item coverage ≠ correctness. Every spec
section has code that mentions the right concepts, but tracing the
actual data flow reveals gaps between what the code claims and what it
does.

## Numbered Issues

### 1. Namespace Bypass on Writes — Middleware Checks Wrong Source [FATAL]

The middleware at L188-195 checks namespace on POST/PUT/PATCH/DELETE
by reading `request.query_params.get("namespace", "shared")`. But the
`/store` endpoint at L270 reads `req.namespace` from the **JSON body**.

An agent with a key scoped to `["shared"]` only can write to any
namespace by:
1. Sending `POST /store?namespace=shared` (passes middleware check)
2. Body: `{"content": "...", "namespace": "personal"}` (stored as personal)

The middleware green-lights "shared" (which the key has), but the
episode is written with "personal" (which the key doesn't have).

**Required fix:** The middleware must check `req.namespace` from the
parsed body, OR the store endpoint must validate `has_namespace(key_info,
req.namespace)` before enqueueing. Do both — defense in depth.

### 2. Namespace Bypass on Reads — Search Has No Enforcement [FATAL]

The search endpoint at L284-313 accepts `namespace` as an optional
query param. If not provided, `ns_filter = ""` — ALL namespaces are
searched. The middleware only checks namespace on writes, not reads.

An agent with a key scoped to `["personal"]` can read ALL facts from
ALL namespaces (including other agents' `personal` namespaces) by
simply omitting the `?namespace=` query param.

This defeats the entire multi-agent isolation model. The spec says
namespaces are "soft boundaries enforced at the API/MCP layer" — but
they're not enforced on reads at all.

**Required fix:** The search endpoint must:
1. Extract `key_info` from `request.state.key_info`
2. If the key has only one namespace, force `namespace` to that value
3. If the key has multiple, and `namespace` param is provided, check
   `has_namespace_access(key_info, namespace)`
4. If no `namespace` param, filter to ALL key-allowed namespaces
   (add `WHERE f.namespace IN $allowed_namespaces`)

### 3. TEMPR: No Timeout Despite DEFAULT_TIMEOUT Defined [SERIOUS]

`tempr.py` defines `DEFAULT_TIMEOUT = 2.0` at L30 and the docstring
says "Each strategy has a 2s timeout." But `tempr_search()` at L341
runs strategies **sequentially** with try/except — no `asyncio.wait_for`,
no threading timeout, no signal-based timeout. If a Neo4j query hangs
(connection pool exhaustion, slow query, deadlock), the entire search
hangs indefinitely.

The strategies are also not parallel despite the docstring claiming
"Runs 5 strategies in parallel (conceptually)." The word
"conceptually" is doing heavy lifting — they're sequential.

**Required fix:** Wrap each strategy call in
`asyncio.wait_for(asyncio.to_thread(strategy_fn), timeout=2.0)` or
use concurrent.futures.ThreadPoolExecutor with timeout. Log and skip
strategies that time out.

### 4. "Rate Limiting" Middleware Has No Rate Limiting [SERIOUS]

The middleware function is named `auth_and_rate_limit_middleware`
but only implements auth + namespace check. There is:
- No token bucket
- No Redis-based request counter
- No per-key rate tracking
- No 429 response with Retry-After header
- No distinction between read and write rate limits

The spec §15 mentions per-key token bucket rate limiting. A single
agent can make unlimited requests, exhausting Neo4j connections or
LLM capacity.

**Required fix:** Implement per-key rate limiting using Redis INCR with
TTL (sliding window) or a token bucket. Return 429 with Retry-After.
Separate limits for reads (higher) vs writes (lower).

### 5. No DLQ/Retry for Failed Ingestion [SERIOUS]

When `process_episode()` fails (LLM extraction returns invalid JSON,
Neo4j write fails, network error), the consumer:
1. Logs the error
2. Sleeps 1 second
3. Moves to the next item in the queue

The failed episode is **gone** — removed from the Redis queue by
`rpop`, never retried, never stored for later analysis. There is no
Dead Letter Queue, no retry counter, no backoff.

For a memory system whose entire purpose is not forgetting, this is
ironic data loss.

**Required fix:** Push failed episodes to a Redis list
`ferrite:failed_queue` with the error message and retry count. After
3 failures, move to `ferrite:dead_letter` for manual inspection.

### 6. certainty Field Type Mismatch — Always 0.0 in Search [SERIOUS]

`FactBase.certainty` is `Literal["stated", "inferred", "speculative"]`
(a string). The search query returns `f.certainty AS certainty` from
Neo4j, which stores this string. `SearchResult.certainty` is `float`.

The API code coerces: `float("stated")` → ValueError → 0.0. So EVERY
search result has `certainty = 0.0`. The field is dead weight — it
conveys no information to the caller.

This means the search API cannot distinguish high-certainty facts from
speculative ones, which defeats the epistemic ranking model.

**Required fix:** Either:
1. Store a numeric certainty (0.0-1.0) in Neo4j alongside the string
   label, and return that in search
2. Or return the string label in a separate field and map
   `stated=1.0, inferred=0.7, speculative=0.4` in the API layer

### 7. No Extraction Retry — LLM JSON Failures Lose Episodes [SERIOUS]

`parse_extraction_response()` raises `ValueError` on invalid JSON.
`extract()` propagates this. `process_episode()` catches it, logs
it, and the episode is marked as "processed" and removed from the LRU.

LLMs are non-deterministic — a prompt that works 95% of the time will
fail 5% of the time. Those 5% of episodes are silently lost.

**Required fix:** Add 1 retry with a stricter prompt ("Return ONLY
valid JSON, no prose"). If still failing, store the raw episode in
the DLQ for later re-extraction.

## What's Genuinely Better Than V2

- **SQLite key store** is real — SHA-256 hashing, scopes, namespaces,
  create/revoke/list. This is production-grade key management.
- **LRU read-your-own-writes** is properly implemented — thread-safe
  with `threading.Lock`, TTL-based cleanup, keyword-overlap scoring,
  `pending_ingestion` flag in search results.
- **Async consumer** runs in-process, polls Redis, handles
  `asyncio.CancelledError` on shutdown. Clean lifecycle.
- **Quality gates** are well-designed — session gate (≥2 turns, error
  state) and assertion gate (assertion_source validation) are cheap
  and correct.
- **MCP HTTP transport** is wired with StreamableHTTPServerTransport.
- **Config YAML** loads from `ferrite.yaml` with env var overrides.
- **Backup/restore scripts** are complete and handle the macOS volume
  issue (F-7) correctly.
- **49/49 spec items** have implementation code.
- **214 tests pass** (176 unit + 38 E2E).

## Verdict: REJECT

Two FATAL namespace bypasses make this unsafe for multi-agent use.
Four SERIOUS issues cause data loss or unbounded latency.

**To move to APPROVE for V4, the implementation must:**

1. Fix namespace enforcement on writes — check body, not query param
2. Fix namespace enforcement on reads — filter by key-allowed namespaces
3. Implement per-strategy timeouts in TEMPR (use the DEFAULT_TIMEOUT
   that's already defined)
4. Implement actual rate limiting (not just the function name)
5. Add DLQ for failed ingestion episodes
6. Fix certainty field type (string in Neo4j vs float in API)
7. Add extraction retry with stricter prompt on JSON parse failure
8. Make TEMPR strategies actually parallel (asyncio.gather or
   ThreadPoolExecutor)
