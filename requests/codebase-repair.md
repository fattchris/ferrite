# Ferrite Codebase Repair — Fix 31 Audit Issues

Fix the following issues identified in a full code quality audit. Each issue is categorized and prioritized. Fix ALL of them in this pass.

## CRITICAL fixes (do these first)

### 1. Hardcoded Neo4j password "ferrite123" in 3 files
- `scripts/seed.py:31` — `NEO4J_PASSWORD = "ferrite123"` hardcoded constant. Change to read from `os.environ.get("NEO4J_PASSWORD")` or import from `ferrite.config.get_settings()`.
- `scripts/health_check.sh:17` — `-p ferrite123` in cleartext. Change to read from `.env` file: `source .env && echo "$NEO4J_PASSWORD"` or use `grep NEO4J_PASSWORD .env | cut -d= -f2-`.
- `src/ferrite/mcp_server.py:72` — default `"ferrite123"` fallback. Change to use `get_settings().NEO4J_PASSWORD`.

### 2. No connection pooling — multiple Neo4j driver instances
- `src/ferrite/ingestion.py:151` creates a driver
- `src/ferrite/api.py:182` creates a separate schema_driver in startup hook
- `src/ferrite/main.py:44` creates another schema_driver
- `src/ferrite/mcp_server.py:89` creates a singleton via `_get_driver()`
- `src/ferrite/mcp_server.py:742` creates a new IngestionPipeline (with new driver) per ingest call

Fix: Create a single shared Neo4j driver singleton. Add a `get_driver()` function (in config.py or a new `db.py` module) that lazily creates one driver instance and reuses it. All modules import and call `get_driver()` instead of creating their own. The MCP server's `_ingest()` must reuse a shared pipeline, not create one per call.

### 3. MCP server creates new IngestionPipeline per ingest call
- `src/ferrite/mcp_server.py:734-771` — `_ingest()` creates a new `IngestionPipeline` (with new Neo4j driver and Redis client) on every call.

Fix: Create the pipeline once at module level or as a lazily-initialized singleton. Reuse it for all ingest calls.

### 4. N+1 entity canonicalization — fetches ALL entities for cosine similarity in Python
- `src/ferrite/canonicalize.py:99-112` — `resolve_entity` fetches ALL entities with embeddings and computes cosine similarity in Python. O(n) over entire DB.

Fix: Use Neo4j's vector index for ANN search, similar to how `src/ferrite/vector_store.py` already does for facts. Create a vector index on Entity embeddings and use `db.index.vector.queryNodes`.

### 5. Bare `except Exception:` everywhere — 25+ instances that silently swallow errors
Files and approximate line numbers (fix ALL instances in each file):
- `src/ferrite/api.py` — lines 169, 209, 277, 634, 641, 758, 782, 831, 907, 919, 924
- `src/ferrite/ingestion.py` — lines 215, 243, 310, 315, 543
- `src/ferrite/extractor.py` — line 205
- `src/ferrite/embeddings.py` — line 58
- `src/ferrite/observability.py` — lines 41, 52, 65, 86, 107, 127, 189, 207
- `src/ferrite/tempr.py` — lines 89, 150, 198, 368, 462
- `src/ferrite/query.py` — line 801
- `src/ferrite/mental_models.py` — line 183

Fix: For each bare `except Exception:` block:
- If it does `pass` without logging, add `logger.warning(f"...: {e}")` or `logger.debug(...)`.
- If it catches a specific error type, narrow to that type (e.g., `except json.JSONDecodeError:`, `except neo4j.exceptions.ServiceUnavailable:`).
- Never silently `pass` — always log at minimum at DEBUG level.

## MEDIUM fixes

### 6. Port mismatch: config says 8000, main.py hardcodes 8000, health check expects 8001
- `src/ferrite/config.py:54` — `SERVER_PORT: int = 8000`
- `src/ferrite/main.py:72` — `port=8000` hardcoded
- `scripts/health_check.sh` — expects 8001

Fix: Make `main.py` use `settings.SERVER_PORT`. Set `SERVER_PORT` default to 8001 in config.py to match Docker. Update `main.py` to `port=settings.SERVER_PORT`.

### 7. Queue key mismatch — health check always reports 0 queue depth
- `src/ferrite/ingestion.py:40` — `QUEUE_KEY = "ferrite:ingestion:queue"`
- `src/ferrite/observability.py:61` — checks `"ferrite:queue"`

Fix: Use the same constant. Import `QUEUE_KEY` from ingestion.py in observability.py, or define it in config.py and import in both.

### 8. Dead in-memory rate limiter never called
- `src/ferrite/api.py:84-115` — `_rate_limit_store` and `_check_rate_limit` are dead code, replaced by Redis-based `check_rate_limit` from `rate_limit.py`.

Fix: Remove `_rate_limit_store` and `_check_rate_limit` entirely from api.py. Also remove the hardcoded `admin_keys = {"admin", "ferrite-admin"}` at line 94.

### 9. LLM timeout hardcoded at 120s in 2 places
- `src/ferrite/api.py:157` — `timeout=120`
- `src/ferrite/mcp_server.py:121` — `timeout=120`

Fix: Add `LLM_TIMEOUT: int = 120` to `config.py` Settings. Use `settings.LLM_TIMEOUT` in both places.

### 10. Embedding defaults ignored — OllamaEmbedder hardcodes its own
- `src/ferrite/embeddings.py:19-21` — `DEFAULT_MODEL = "nomic-embed-text"`, `DEFAULT_HOST = "http://localhost:11434"`, `EMBEDDING_DIM = 768`
- `config.py` has `EMBED_BASE_URL` and `EMBED_MODEL` but they're not used by OllamaEmbedder.

Fix: Pass `settings.EMBED_BASE_URL` and `settings.EMBED_MODEL` to `OllamaEmbedder()` when it's instantiated. Make the defaults in embeddings.py fallback only.

### 11. Model default mismatch: mcp_server defaults to glm-5.2, config defaults to gpt-4o-mini
- `src/ferrite/mcp_server.py:76` — `LLM_MODEL = os.environ.get("LLM_MODEL", "glm-5.2")`
- `src/ferrite/config.py:41` — `LLM_MODEL` defaults to `gpt-4o-mini`

Fix: Make `mcp_server.py` use `get_settings().LLM_MODEL` instead of reading env directly. Make config.py default `"glm-5.2"` to match the actual deployment.

### 12. Sync process_episode blocks async event loop
- `src/ferrite/ingestion.py:187-217` — `start_consumer` is async but calls `self.process_episode(episode_id)` synchronously.

Fix: Wrap the call: `await asyncio.get_event_loop().run_in_executor(None, self.process_episode, episode_id)`. Add `import asyncio` at module level.

### 13. _write_fact opens new session per fact — no batching
- `src/ferrite/ingestion.py:442-577` — Each `_write_fact` call opens `with self.driver.session()` and runs 6-7 Cypher queries. For 20 facts = 120+ transactions.

Fix: Reuse a single session for all facts in an episode. Pass the session into `_write_fact` or batch the writes.

### 14. Redis INCR+EXPIRE not atomic
- `src/ferrite/rate_limit.py:39-41` — `incr` then `expire` is not atomic. Crash between them = counter persists forever.

Fix: Use a Redis Lua script or pipeline to make INCR + EXPIRE atomic.

### 15. app = create_app() at module level — importing connects to Neo4j/Redis
- `src/ferrite/api.py:931` — `app = create_app()` at module level.

Fix: Move to lazy initialization. Use a `get_app()` function or only call `create_app()` in the `main()` entry point.

### 16. N+1 in temporal.py supersession detection
- `src/ferrite/temporal.py:47-57` — Opens new session per fact for object lookup.

Fix: Rewrite as a single Cypher query that returns both the fact and its object in one traversal.

### 17. N+1 in query.py multi-hop traversal
- `src/ferrite/query.py:254-279` — New `session.run()` for EACH entity in the frontier per hop.

Fix: Use a single Cypher query with variable-length path matching (`*1..N`).

### 18. No retry on Neo4j writes or LLM/Ollama HTTP calls
- Neo4j writes in `ingestion.py` and `consolidator.py` have no retry.
- LLM calls in `embeddings.py:49`, `mcp_server.py:121`, `api.py:157` use `urllib.request.urlopen()` with no retry.

Fix: Add a simple retry decorator with exponential backoff for transient errors. For Neo4j: retry on `ServiceUnavailable`. For HTTP: retry on connection errors and 5xx. Max 3 retries.

### 19. Key store SQLite path inconsistency
- `src/ferrite/key_store.py:18-21` — defaults to `Path.home() / "ferrite" / "data" / "keys.db"`
- `src/ferrite/api.py:225` — checks `os.path.join(os.path.dirname(__file__), "..", "..", "data", "keys.db")`

Fix: Add `KEYS_DB_PATH` to config.py Settings. Use it in both key_store.py and api.py.

### 20. restore.sh uses wrong default compose file
- `scripts/restore.sh:9` — defaults to `docker-compose.yml`
- `scripts/backup.sh` — defaults to `docker-compose.prod.yml`

Fix: Change `scripts/restore.sh` default to `docker-compose.prod.yml`.

## LOW fixes (AI slop cleanup)

### 21. __import__("datetime").timedelta(...) in tempr.py
- `src/ferrite/tempr.py:259, 264` — uses `__import__("datetime").timedelta(days=7)` instead of importing `timedelta`.

Fix: Add `timedelta` to the existing `from datetime import datetime, timezone` import. Replace `__import__("datetime").timedelta(...)` with `timedelta(...)`.

### 22. Duplicate cosine_similarity function
- `src/ferrite/canonicalize.py:36-45` and `src/ferrite/embeddings.py:77-84` — identical function in two modules.

Fix: Keep one in `embeddings.py`, import it in `canonicalize.py`.

### 23. Imports inside function bodies instead of module level
- `src/ferrite/ingestion.py:317` — `import json` inside except block (already imported at module level line 15)
- `src/ferrite/ingestion.py:565` — `import uuid` inside `_write_fact`
- `src/ferrite/api.py:137, 261, 574` — `import json as _json` repeated in nested functions
- `src/ferrite/api.py:196` — `import asyncio as _asyncio` inside async function
- `src/ferrite/tempr.py:251, 288` — `import calendar` inside function, twice

Fix: Move all these to module-level imports. Remove the redundant local imports.

### 24. Dead datetime.now() results discarded
- `src/ferrite/observability.py:71` — computes threshold but never uses it
- `src/ferrite/mental_models.py:251` — `datetime.now(timezone.utc).isoformat()` result not assigned

Fix: In observability.py, actually compare `record['last_ingest']` against the computed threshold. In mental_models.py, remove the dead line or use it in the query.

### 25. dict used as untyped bag for extraction results
- `src/ferrite/extractor.py:80, 101, 180` — returns bare `dict`
- `src/ferrite/ingestion.py:219, 349` — `fact_data: dict`, `entity_cache: dict[str, object]`

Fix: Define Pydantic models `ExtractionResult`, `ExtractedEntity`, `ExtractedFact` in `models.py`. Use them as return types in extractor.py and as parameter types in ingestion.py.

### 26. Callable without type parameters
- `src/ferrite/ingestion.py:147-148` — `embedding_func: Optional[Callable]`, `llm_client: Optional[Callable]`
- `src/ferrite/extractor.py:6, 180` — `Callable` without signature

Fix: Use `Callable[[str], list[float]]` for embedding_func and `Callable[[str, str], str]` for llm_client.

### 27. driver parameter untyped across ~20 function signatures
- `canonicalize.py`, `temporal.py`, `query.py`, `consolidator.py` — every function taking `driver` has no type annotation.

Fix: Import `from neo4j import Driver` (or define a Protocol) and annotate all `driver` parameters as `driver: Driver`.

### 28. dict | None return from validate_token instead of proper model
- `src/ferrite/key_store.py:164` — returns `dict | None`
- `src/ferrite/api.py:77` — `_validate_request_token` returns `dict | None`

Fix: Define a `KeyInfo` Pydantic model in `models.py` with fields `key_id: str`, `agent_name: str`, `scopes: list[str]`, `namespaces: list[str]`. Return `Optional[KeyInfo]`.

### 29. embedder parameter untyped across query/tempr/eval
- `src/ferrite/query.py:302, 365, 413`, `src/ferrite/tempr.py:376`, `src/ferrite/eval.py:75` — bare `embedder` parameter.

Fix: Define an `Embedder` Protocol with an `embed(text: str) -> list[float]` method. Annotate all `embedder` parameters.

### 30. Any type annotations in circuit breaker
- `src/ferrite/circuit_breaker.py:24, 83, 85, 86, 148` — `Callable[..., Any]`, `fallback: Any`, return `Any`, `get_state() -> dict`

Fix: Use generics: `def call[T](self, func: Callable[..., T], *args, fallback: T = None, **kwargs) -> T`. Define a `CircuitState` model for `get_state()`.

### 31. Dead retry loop in ingestion.py
- `src/ferrite/ingestion.py:239-254` — `for attempt in range(2)` never iterates to attempt 1 because retry happens inline.

Fix: Rewrite as a proper retry loop where each iteration is a real attempt, or remove the `for` loop and use clear inline retry with comments.

## Constraints

- Do NOT change any public API endpoint signatures or response shapes.
- Do NOT remove any functionality — only fix, type, and refactor.
- All existing tests must pass after changes.
- Use `from __future__ import annotations` if needed for forward references.
- Keep the same file structure — don't create new modules unless specified (db.py for driver singleton is OK).
- The config.py Settings class is the single source of truth for all configuration.

## Done means

- Zero hardcoded passwords in the codebase.
- Exactly one Neo4j driver instance (shared singleton).
- Zero bare `except Exception: pass` blocks.
- All tests pass.
- `docker compose -f docker-compose.prod.yml build ferrite-api` succeeds.
