"""FastAPI application with Ferrite KG endpoints."""

import logging
import os
import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .ingestion import IngestionPipeline
from .key_store import (
    create_key as ks_create_key,
)
from .key_store import (
    has_namespace_access as ks_has_namespace,
)
from .key_store import (
    has_scope as ks_has_scope,
)
from .key_store import (
    init_db as ks_init_db,
)
from .key_store import (
    list_keys as ks_list_keys,
)
from .key_store import (
    revoke_key as ks_revoke_key,
)
from .key_store import (
    validate_token as ks_validate_token,
)
from .models import (
    HealthResponse,
    SearchResponse,
    SearchResult,
    StoreRequest,
    StoreResponse,
)
from .rate_limit import check_rate_limit
from .temporal import get_history_as_of_knowledge, get_history_as_of_world

logger = logging.getLogger(__name__)
settings = get_settings()

# Auth: SQLite key store (§6.1) + env-based admin key for backward compat.
# Public endpoints: health, metrics, circuit-breaker, root (Web UI).
_PUBLIC_ENDPOINTS = {
    "/", "/health", "/metrics", "/metrics/prometheus",
    "/circuit-breaker", "/circuit-breaker/reset",
    "/install", "/install/generate-secrets", "/install/verify",
    "/wiki",
}
# Key management endpoints require admin scope.
_ADMIN_ENDPOINTS = {"/keys", "/keys/{key_id}/revoke"}


def _extract_token(request: Request) -> str:
    """Extract bearer token from Authorization header or X-API-Key."""
    auth_header = request.headers.get("Authorization", "")
    x_api_key = request.headers.get("X-API-Key", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    if x_api_key:
        return x_api_key
    return ""


def _validate_request_token(request: Request) -> dict | None:
    """Validate the token from the request, returns key_info or None."""
    token = _extract_token(request)
    if not token:
        return None
    return ks_validate_token(token)

# Rate limiting storage (in-memory for MVP; Redis in production)
_rate_limit_store: dict[str, dict] = {}


def _check_rate_limit(api_key: str, is_write: bool) -> bool:
    """Token bucket rate limiter. Returns True if request is allowed."""
    if not api_key:
        api_key = "anonymous"

    # Admin keys are exempt
    admin_keys = {"admin", "ferrite-admin"}
    if api_key in admin_keys:
        return True

    now = time.time()
    limit = settings.WRITE_RATE_LIMIT if is_write else settings.READ_RATE_LIMIT
    window = 60.0  # 1 minute

    if api_key not in _rate_limit_store:
        _rate_limit_store[api_key] = {"read": [], "write": []}

    bucket_key = "write" if is_write else "read"
    bucket = _rate_limit_store[api_key][bucket_key]

    # Remove timestamps outside the window
    bucket[:] = [t for t in bucket if now - t < window]

    if len(bucket) >= limit:
        return False

    bucket.append(now)
    return True


def create_app(pipeline: Optional[IngestionPipeline] = None) -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="Ferrite",
        description="Temporal Knowledge Graph System",
        version="0.1.0",
    )

    # Initialize pipeline if not provided
    if pipeline is None:
        try:
            # Build LLM client callable for extraction
            def _make_llm_client():
                llm_key = settings.LLM_API_KEY
                llm_base = getattr(settings, "LLM_BASE_URL", "http://localhost:4000/v1")
                llm_model = getattr(settings, "LLM_MODEL", "glm-5.2")
                if not llm_key:
                    return None

                import json as _json
                import urllib.request as _urllib

                def _llm_client(system_prompt: str, user_prompt: str) -> str:
                    data = _json.dumps({
                        "model": llm_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.1,
                    }).encode()
                    req = _urllib.Request(
                        f"{llm_base}/chat/completions",
                        data=data,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {llm_key}",
                        },
                    )
                    with _urllib.urlopen(req, timeout=120) as r:
                        return _json.loads(r.read())["choices"][0]["message"]["content"]

                return _llm_client

            pipeline = IngestionPipeline(
                redis_url=settings.REDIS_URL,
                neo4j_uri=settings.NEO4J_URI,
                neo4j_user=settings.NEO4J_USER,
                neo4j_password=settings.NEO4J_PASSWORD,
                llm_client=_make_llm_client(),
            )
        except Exception as e:
            logger.warning(f"Could not initialize pipeline: {e}")
            pipeline = None

    # Initialize SQLite key store on startup
    ks_init_db()

    # Initialize Neo4j schema (indexes, constraints) on startup
    @app.on_event("startup")
    async def init_neo4j_schema():
        try:
            from .schema import init_schema
            from neo4j import GraphDatabase
            schema_driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            )
            init_schema(schema_driver)
            schema_driver.close()
            logger.info("Neo4j schema initialized")
        except Exception as e:
            logger.warning(f"Could not initialize Neo4j schema: {e}")

    # Start in-proc async consumer (§7.1)
    @app.on_event("startup")
    async def start_ingestion_consumer():
        if pipeline is not None:
            import asyncio as _asyncio
            app.state.consumer_task = _asyncio.create_task(
                pipeline.start_consumer(poll_interval=1.0)
            )
            logger.info("In-proc ingestion consumer started")

    @app.on_event("shutdown")
    async def stop_ingestion_consumer():
        task = getattr(app.state, "consumer_task", None)
        if task:
            task.cancel()
            try:
                await task
            except Exception:
                pass

    @app.middleware("http")
    async def auth_and_rate_limit_middleware(request: Request, call_next):
        path = request.url.path

        # --- Auth check (§6.1: SQLite key store) ---
        if path not in _PUBLIC_ENDPOINTS:
            key_info = _validate_request_token(request)

            # No valid token — check if auth is entirely disabled (dev mode)
            env_key = os.environ.get("FERRITE_API_KEY", "")
            if not env_key and not os.path.exists(
                os.environ.get(
                    "FERRITE_KEYS_DB",
                    os.path.join(os.path.dirname(__file__), "..", "..", "data", "keys.db"),
                )
            ):
                # Dev mode: no keys configured, auth disabled
                key_info = {
                    "key_id": "dev",
                    "agent_name": "dev",
                    "scopes": ["read", "write", "admin"],
                    "namespaces": ["shared", "personal", "e2e-test"],
                }

            if key_info is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key"},
                )

            # Admin endpoint check (§6.2)
            if path.startswith("/keys") and not ks_has_scope(key_info, "admin"):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Admin scope required"},
                )

            # Namespace enforcement (§6.3): check on write operations
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                # Check namespace from query param AND body (F-1 fix)
                ns_param = request.query_params.get("namespace", "shared")
                if not ks_has_namespace(key_info, ns_param):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": f"Namespace '{ns_param}' not allowed for this key"},
                    )
                # Also check body namespace for POST /store (F-1 fix)
                if request.headers.get("content-type", "").startswith("application/json"):
                    try:
                        import json as _json
                        body = _json.loads(request._body) if hasattr(request, "_body") else None
                        if body and "namespace" in body:
                            body_ns = body["namespace"]
                            if body_ns != ns_param and not ks_has_namespace(key_info, body_ns):
                                return JSONResponse(
                                    status_code=403,
                                    content={
                                "detail": (
                                    f"Namespace '{body_ns}' in body "
                                    f"not allowed for this key"
                                )
                            },
                                )
                            # Override query param with body namespace
                            ns_param = body_ns
                    except Exception:
                        pass  # Body parse failure handled by endpoint validation

            # Store key_info in request state for downstream handlers
            request.state.key_info = key_info

            # Rate limiting (F-4 fix): per-key sliding window via Redis
            if key_info and hasattr(pipeline, "redis_client") and pipeline.redis_client:
                is_write = request.method in ("POST", "PUT", "PATCH", "DELETE")
                allowed, retry_after = check_rate_limit(
                    pipeline.redis_client,
                    key_info.get("key_id", "anonymous"),
                    is_write=is_write,
                )
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Rate limit exceeded"},
                        headers={
                            "Retry-After": str(retry_after or 10),
                            "X-RateLimit-Limit": "20" if is_write else "100",
                        },
                    )

        return await call_next(request)

    # --- Key Management API (§6.2) ---

    @app.post("/keys")
    async def create_api_key(request: Request):
        """Create a new API key (admin scope required).

        Body: {agent_name, scopes?, namespaces?}
        Returns: {key_id, token, agent_name, scopes, namespaces}
        Token is returned ONCE — only the hash is stored.
        """
        body = await request.json()
        agent_name = body.get("agent_name", "")
        scopes = body.get("scopes", ["read", "write"])
        namespaces = body.get("namespaces", ["shared"])

        if not agent_name:
            raise HTTPException(status_code=400, detail="agent_name required")

        return ks_create_key(agent_name, scopes=scopes, namespaces=namespaces)

    @app.get("/keys")
    async def list_api_keys(active_only: bool = True):
        """List all API keys with status."""
        return ks_list_keys(active_only=active_only)

    @app.post("/keys/{key_id}/revoke")
    async def revoke_api_key(key_id: str):
        """Revoke an API key by ID."""
        revoked = ks_revoke_key(key_id)
        if not revoked:
            raise HTTPException(status_code=404, detail="Key not found or already revoked")
        return {"status": "revoked", "key_id": key_id}

    # --- Core endpoints ---

    @app.post("/store", response_model=StoreResponse)
    async def store(request: Request, req: StoreRequest):
        """Queue content for ingestion."""
        from .circuit_breaker import get_circuit_breaker

        # Defense in depth: validate namespace against key (F-1 fix)
        key_info = getattr(request.state, "key_info", None)
        if key_info and not ks_has_namespace(key_info, req.namespace):
            raise HTTPException(
                status_code=403,
                detail=f"Namespace '{req.namespace}' not allowed for this key",
            )

        breaker = get_circuit_breaker()
        if not breaker.can_execute():
            raise HTTPException(
                status_code=503,
                detail="Circuit breaker open — service temporarily unavailable",
            )

        if pipeline is None:
            raise HTTPException(status_code=503, detail="Pipeline not available")

        try:
            from .metrics import get_metrics
            from .models import Episode

            episode = Episode(
                content=req.content,
                content_type=req.content_type,
                source=req.source,
                namespace=req.namespace or settings.NAMESPACE_DEFAULT,
            )
            episode_id = pipeline.enqueue(episode)
            breaker.call(lambda: None)  # record success
            get_metrics().increment("ingestion_count", tags={"namespace": req.namespace})
            return StoreResponse(episode_id=episode_id, status="queued")
        except Exception as exc:
            err = exc  # capture for lambda closure
            breaker.call(
                lambda: (_ for _ in ()).throw(err),
                fallback=None,
            )
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/search", response_model=SearchResponse)
    async def search(
        request: Request,
        query: str = Query(..., min_length=1),
        namespace: Optional[str] = Query(None),
        limit: int = Query(10, le=100),
    ) -> SearchResponse:
        """Search facts by BM25 + semantic hybrid on Fact.statement.

        Merges LRU pending_ingestion hits for read-your-own-writes (§6.4, A9).
        Namespace enforcement (F-2 fix): reads are filtered by key-allowed namespaces.
        """
        if pipeline is None:
            raise HTTPException(status_code=503, detail="Pipeline not available")

        from .metrics import get_metrics

        search_start = time.time()

        # Namespace enforcement on reads (F-2 fix)
        key_info = getattr(request.state, "key_info", None)
        ns_params: dict = {}
        if key_info:
            allowed_ns = key_info.get("namespaces", ["shared"])
            if namespace:
                if not ks_has_namespace(key_info, namespace):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Namespace '{namespace}' not allowed for this key",
                    )
                ns_filter = "AND f.namespace = $namespace"
                ns_params["namespace"] = namespace
            else:
                # Filter to key-allowed namespaces only (F-2 fix)
                ns_filter = "AND f.namespace IN $allowed_namespaces"
                ns_params["allowed_namespaces"] = allowed_ns
        else:
            ns_filter = "AND f.namespace = $namespace" if namespace else ""
            if namespace:
                ns_params["namespace"] = namespace

        with pipeline.driver.session() as session:
            result = session.run(
                f"""
                CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $search_query)
                YIELD node AS f, score
                WHERE f:Fact
                {ns_filter}
                RETURN f.id AS id, f.statement AS statement,
                       f.certainty AS certainty,
                       f.assertion_source AS source,
                       f.valid_at AS valid_at
                ORDER BY score DESC
                LIMIT $limit
                """,
                search_query=query,
                namespace=namespace,
                **ns_params,
                limit=limit,
            )

            results = []
            # Certainty mapping (F-6 fix): string label → numeric value
            CERTAINTY_MAP = {"stated": 1.0, "inferred": 0.7, "speculative": 0.4}
            for r in result:
                # certainty in Neo4j is a string label, map to numeric (F-6 fix)
                cert_raw = r["certainty"]
                cert_label = str(cert_raw) if cert_raw else "stated"
                cert_val = CERTAINTY_MAP.get(cert_label, 0.0)
                results.append(
                    SearchResult(
                        id=r["id"],
                        statement=r["statement"],
                        certainty=cert_val,
                        certainty_label=cert_label,
                        source=str(r["source"]) if r["source"] else "",
                        valid_at=str(r["valid_at"]) if r["valid_at"] else "",
                    )
                )

        # Merge LRU pending_ingestion hits (§6.4, A9)
        from .ingestion import get_lru
        lru_hits = get_lru().search(query)
        for hit in lru_hits[:limit]:
            results.append(
                SearchResult(
                    id=hit["id"],
                    statement=hit["statement"],
                    certainty=0.0,
                    source="pending_ingestion",
                    valid_at="",
                )
            )

        # Record search metrics (P-5 fix)
        search_latency_ms = (time.time() - search_start) * 1000
        get_metrics().increment("query_count")
        get_metrics().observe("query_latency_ms", search_latency_ms)
        get_metrics().gauge("queue_depth", float(pipeline.get_queue_depth()))

        return SearchResponse(results=results[:limit])


    @app.get("/entities")
    async def list_entities(
        limit: int = Query(100, le=500),
        offset: int = Query(0, ge=0),
    ):
        """List all entities with fact counts."""
        if pipeline is None:
            raise HTTPException(status_code=503, detail="Pipeline not available")
        with pipeline.driver.session() as session:
            result = session.run(
                """
                MATCH (e:Entity)
                OPTIONAL MATCH (e)<-[:SUBJECT]-(f:Fact)
                RETURN e.id AS id, e.type AS type, e.name AS name,
                       e.summary AS summary, count(f) AS fact_count
                ORDER BY fact_count DESC, e.name ASC
                SKIP $offset LIMIT $limit
                """,
                offset=offset,
                limit=limit,
            )
            entities = [
                {
                    "id": r["id"],
                    "type": r["type"],
                    "name": r["name"],
                    "summary": r["summary"],
                    "fact_count": r["fact_count"],
                }
                for r in result
            ]
            # Get total count
            total = session.run("MATCH (e:Entity) RETURN count(e) AS c").single()["c"]
        return {"entities": entities, "total": total}

    @app.get("/entities/{entity_id}")
    async def get_entity(
        entity_id: str,
        namespace: Optional[str] = Query(None),
    ):
        """Get full entity node with edges, provenance, and temporal history.
        Edges filtered by namespace (via neighbor Fact's namespace)."""
        if pipeline is None:
            raise HTTPException(status_code=503, detail="Pipeline not available")

        ns_filter = "AND f.namespace = $namespace" if namespace else ""

        with pipeline.driver.session() as session:
            # Get entity
            entity_result = session.run(
                """
                MATCH (e:Entity {id: $entity_id})
                RETURN e.id AS id, e.type AS type, e.name AS name, e.summary AS summary
                """,
                entity_id=entity_id,
            )
            entity_record = entity_result.single()

            if not entity_record:
                raise HTTPException(status_code=404, detail="Entity not found")

            # Get facts where entity is subject
            subj_result = session.run(
                f"""
                MATCH (e:Entity {{id: $entity_id}})<-[:SUBJECT]-(f:Fact)
                WHERE true
                {ns_filter}
                RETURN f.id AS id, f.statement AS statement, f.predicate AS predicate,
                       f.certainty AS certainty, f.epistemic_state AS epistemic_state,
                       f.valid_at AS valid_at, f.invalid_at AS invalid_at,
                       f.recorded_at AS recorded_at, f.namespace AS namespace
                """,
                entity_id=entity_id,
                namespace=namespace,
            )

            # Get facts where entity is object
            obj_result = session.run(
                f"""
                MATCH (e:Entity {{id: $entity_id}})<-[:OBJECT]-(f:Fact)
                WHERE true
                {ns_filter}
                RETURN f.id AS id, f.statement AS statement, f.predicate AS predicate,
                       f.certainty AS certainty, f.epistemic_state AS epistemic_state,
                       f.valid_at AS valid_at, f.invalid_at AS invalid_at,
                       f.recorded_at AS recorded_at, f.namespace AS namespace
                """,
                entity_id=entity_id,
                namespace=namespace,
            )

            return {
                "entity": dict(entity_record),
                "facts_as_subject": [dict(r) for r in subj_result],
                "facts_as_object": [dict(r) for r in obj_result],
            }

    @app.get("/history/{entity_id}")
    async def get_history(
        entity_id: str,
        at_time: Optional[str] = Query(None),
        mode: str = Query("knowledge", pattern="^(knowledge|world)$"),
        namespace: Optional[str] = Query(None),
    ):
        """Temporal query: as_of_knowledge or as_of_world."""
        if pipeline is None:
            raise HTTPException(status_code=503, detail="Pipeline not available")

        if at_time:
            try:
                dt = datetime.fromisoformat(at_time)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid datetime format. Use ISO 8601.",
                )
        else:
            dt = datetime.utcnow()

        if mode == "knowledge":
            facts = get_history_as_of_knowledge(
                pipeline.driver, entity_id, dt, namespace
            )
        else:
            facts = get_history_as_of_world(
                pipeline.driver, entity_id, dt, namespace
            )

        return {"entity_id": entity_id, "mode": mode, "at_time": dt.isoformat(), "facts": facts}

    @app.get("/health", response_model=HealthResponse)
    async def health():
        """System health: Neo4j, Redis, queue depth."""
        neo4j_status = "ok"
        redis_status = "ok"
        queue_depth = 0

        if pipeline is None:
            return HealthResponse(
                neo4j="unavailable",
                redis="unavailable",
                queue_depth=0,
            )

        # Check Neo4j
        try:
            with pipeline.driver.session() as session:
                session.run("RETURN 1").consume()
        except Exception:
            neo4j_status = "error"

        # Check Redis
        try:
            pipeline.redis_client.ping()
            queue_depth = pipeline.get_queue_depth()
        except Exception:
            redis_status = "error"

        return HealthResponse(
            neo4j=neo4j_status,
            redis=redis_status,
            queue_depth=queue_depth,
        )

    @app.get("/metrics")
    async def metrics():
        """Detailed metrics and health checks (JSON)."""
        from .metrics import get_metrics
        from .observability import HealthMonitor

        result: dict = {"metrics": get_metrics().snapshot()}

        if pipeline is not None:
            monitor = HealthMonitor(pipeline.driver, pipeline.redis_client)
            result["health"] = monitor.run_all()

        return result

    @app.get("/metrics/prometheus", include_in_schema=False)
    async def prometheus_metrics():
        """Prometheus text-format metrics endpoint (P-5 fix).

        Exposes counters, gauges, and histograms in the Prometheus
        text exposition format for scraping by Prometheus/Grafana.
        """
        from .metrics import get_metrics
        from .observability import HealthMonitor

        def _quote_tags(tags_str: str) -> str:
            """Quote tag values for Prometheus label compliance."""
            parts = []
            for t in tags_str.split(","):
                if "=" in t:
                    k, v = t.split("=", 1)
                    parts.append(f'{k}="{v}"')
                else:
                    parts.append(t)
            return ",".join(parts)

        snap = get_metrics().snapshot()
        lines: list[str] = [
            "# HELP ferrite_info Ferrite build info",
            '# TYPE ferrite_info gauge',
            'ferrite_info{version="0.1.0"} 1',
            "",
        ]

        # Counters
        lines.append("# HELP ferrite_counter_total Counter metrics")
        lines.append("# TYPE ferrite_counter_total counter")
        for key, val in snap.get("counters", {}).items():
            # Parse tag syntax: name{tag=val}
            if "{" in key:
                base, rest = key.split("{", 1)
                tags = _quote_tags(rest.rstrip("}"))
                lines.append(f'ferrite_counter_total{{metric="{base}",{tags}}} {val}')
            else:
                lines.append(f'ferrite_counter_total{{metric="{key}"}} {val}')
        lines.append("")

        # Gauges
        lines.append("# HELP ferrite_gauge Gauge metrics")
        lines.append("# TYPE ferrite_gauge gauge")
        for key, val in snap.get("gauges", {}).items():
            if "{" in key:
                base, rest = key.split("{", 1)
                tags = _quote_tags(rest.rstrip("}"))
                lines.append(f'ferrite_gauge{{metric="{base}",{tags}}} {val}')
            else:
                lines.append(f'ferrite_gauge{{metric="{key}"}} {val}')
        lines.append("")

        # Histograms
        lines.append("# HELP ferrite_histogram_avg Average of histogram observations")
        lines.append("# TYPE ferrite_histogram_avg gauge")
        for key, stats in snap.get("histograms", {}).items():
            if "{" in key:
                base, rest = key.split("{", 1)
                tags = _quote_tags(rest.rstrip("}"))
                label = f'metric="{base}",{tags}'
            else:
                label = f'metric="{key}"'
            lines.append(f'ferrite_histogram_avg{{{label}}} {stats["avg"]}')
            lines.append(f'ferrite_histogram_count{{{label}}} {stats["count"]}')
        lines.append("")

        # Health gauges
        if pipeline is not None:
            monitor = HealthMonitor(pipeline.driver, pipeline.redis_client)
            health = monitor.run_all()
            lines.append("# HELP ferrite_health Service health (1=ok, 0=error)")
            lines.append("# TYPE ferrite_health gauge")
            for check_name, check_val in health.items():
                if isinstance(check_val, dict):
                    status = check_val.get("status", "unknown")
                    lines.append(
                        f'ferrite_health{{check="{check_name}"}} '
                        f'{1 if status == "ok" else 0}'
                    )
                elif isinstance(check_val, str):
                    lines.append(
                        f'ferrite_health{{check="{check_name}"}} '
                        f'{1 if check_val == "ok" else 0}'
                    )
            lines.append("")

            # Queue depth gauge
            try:
                queue_depth = pipeline.get_queue_depth()
                lines.append("# HELP ferrite_queue_depth Ingestion queue depth")
                lines.append("# TYPE ferrite_queue_depth gauge")
                lines.append(f"ferrite_queue_depth {queue_depth}")
            except Exception:
                pass

        return PlainTextResponse(
            "\n".join(lines),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post("/tempr")
    async def tempr_search_endpoint(request: Request):
        """TEMPR multi-strategy retrieval."""
        from .embeddings import OllamaEmbedder
        from .tempr import tempr_search

        body = await request.json()
        query = body.get("query", "")
        limit = body.get("limit", 10)
        include_history = body.get("include_history", False)

        if pipeline is None:
            return {"error": "Pipeline not available"}

        try:
            embedder = OllamaEmbedder()
        except Exception:
            embedder = None

        results = tempr_search(
            pipeline.driver, query, embedder=embedder,
            limit=limit, include_history=include_history,
        )
        return {"results": results, "count": len(results)}

    @app.get("/mental-models")
    async def search_mental_models_endpoint(query: str, limit: int = 5):
        """Search mental models."""
        from .mental_models import search_mental_models

        if pipeline is None:
            return {"error": "Pipeline not available"}
        results = search_mental_models(
            pipeline.driver, query, limit=limit
        )
        return {"results": results, "count": len(results)}

    @app.post("/consolidate")
    async def consolidate_endpoint():
        """Run observation consolidation on pending groups."""
        from .consolidator import consolidate_pending

        if pipeline is None:
            return {"error": "Pipeline not available"}
        count = consolidate_pending(
            pipeline.driver, pipeline.llm_client,
            redis_client=pipeline.redis_client,
        )
        return {"consolidated_groups": count}

    @app.get("/eval")
    async def eval_endpoint():
        """Run eval harness (§13.2, A5)."""
        from .embeddings import OllamaEmbedder
        from .eval import health_check, run_eval

        health = health_check()
        if health["status"] != "ok":
            return {"error": "eval harness not ready", "health": health}

        if pipeline is None:
            return {"error": "Pipeline not available"}

        try:
            embedder = OllamaEmbedder()
        except Exception:
            embedder = None

        return run_eval(pipeline.driver, embedder=embedder)

    @app.get("/circuit-breaker")
    async def circuit_breaker_endpoint():
        """Get circuit breaker state (§8.1)."""
        from .circuit_breaker import get_circuit_breaker
        return get_circuit_breaker().get_state()

    @app.post("/circuit-breaker/reset")
    async def reset_circuit_breaker_endpoint():
        """Manually reset the circuit breaker."""
        from .circuit_breaker import get_circuit_breaker
        get_circuit_breaker().reset()
        return {"status": "reset", "state": "closed"}

    # --- Web UI ---
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        """Serve the Web UI."""
        index = os.path.join(os.path.dirname(__file__), "static", "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        return JSONResponse({"detail": "Web UI not available"}, status_code=404)

    @app.get("/install", include_in_schema=False)
    async def installer_page():
        """Serve the web GUI installer."""
        installer = os.path.join(os.path.dirname(__file__), "static", "install.html")
        if os.path.isfile(installer):
            return FileResponse(installer)
        return JSONResponse({"detail": "Installer not available"}, status_code=404)

    @app.get("/wiki", include_in_schema=False)
    async def wiki_page():
        """Serve the Knowledge Explorer (Obsidian-like wiki view)."""
        wiki = os.path.join(os.path.dirname(__file__), "static", "wiki.html")
        if os.path.isfile(wiki):
            return FileResponse(wiki)
        return JSONResponse({"detail": "Wiki not available"}, status_code=404)

    @app.post("/install/generate-secrets", include_in_schema=False)
    async def generate_secrets(request: Request):
        """Generate secure random secrets for .env file."""
        import secrets as _secrets
        return {
            "neo4j_password": _secrets.token_hex(32),
            "api_key": _secrets.token_hex(32),
        }

    @app.post("/install/verify", include_in_schema=False)
    async def install_verify(request: Request):
        """Verify a running Ferrite deployment."""
        try:
            import httpx as _httpx
            # Check API health
            base = f"http://localhost:{os.environ.get('PORT', '8001')}"
            api_key = os.environ.get("FERRITE_API_KEY", "")

            results = {"api": False, "auth": False, "redis_aof": False, "neo4j": False}

            # Health
            try:
                async with _httpx.AsyncClient() as client:
                    resp = await client.get(f"{base}/health", timeout=5.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        results["api"] = True
                        results["neo4j"] = data.get("neo4j") == "ok"
                        results["redis_aof"] = data.get("redis") == "ok"
            except Exception:
                pass

            # Auth
            try:
                async with _httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{base}/search?query=test&limit=1",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=5.0,
                    )
                    results["auth"] = resp.status_code == 200
            except Exception:
                pass

            all_ok = all(results.values())
            return {"status": "ok" if all_ok else "issues", "checks": results}
        except Exception as e:
            return {"status": "error", "message": str(e), "checks": {}}

    return app


# Module-level app for uvicorn
app = create_app()
