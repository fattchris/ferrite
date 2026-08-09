"""FastAPI application with Ferrite KG endpoints."""

import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .ingestion import IngestionPipeline
from .models import (
    HealthResponse,
    SearchResponse,
    SearchResult,
    StoreRequest,
    StoreResponse,
)
from .temporal import get_history_as_of_knowledge, get_history_as_of_world

logger = logging.getLogger(__name__)
settings = get_settings()

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
            pipeline = IngestionPipeline(
                redis_url=settings.REDIS_URL,
                neo4j_uri=settings.NEO4J_URI,
                neo4j_user=settings.NEO4J_USER,
                neo4j_password=settings.NEO4J_PASSWORD,
            )
        except Exception as e:
            logger.warning(f"Could not initialize pipeline: {e}")
            pipeline = None

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        api_key = request.headers.get("X-API-Key", "")
        is_write = request.method in ("POST", "PUT", "PATCH", "DELETE")

        if not _check_rate_limit(api_key, is_write):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )

        return await call_next(request)

    @app.post("/store", response_model=StoreResponse)
    async def store(req: StoreRequest):
        """Queue content for ingestion."""
        if pipeline is None:
            raise HTTPException(status_code=503, detail="Pipeline not available")

        from .models import Episode

        episode = Episode(
            content=req.content,
            content_type=req.content_type,
            source=req.source,
            namespace=req.namespace or settings.NAMESPACE_DEFAULT,
        )
        episode_id = pipeline.enqueue(episode)
        return StoreResponse(episode_id=episode_id, status="queued")

    @app.get("/search", response_model=SearchResponse)
    async def search(
        query: str = Query(..., min_length=1),
        namespace: Optional[str] = Query(None),
        limit: int = Query(10, le=100),
    ):
        """Search facts by BM25 + semantic hybrid on Fact.statement."""
        if pipeline is None:
            raise HTTPException(status_code=503, detail="Pipeline not available")

        ns_filter = "AND f.namespace = $namespace" if namespace else ""

        with pipeline.driver.session() as session:
            result = session.run(
                f"""
                CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $query)
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
                query=query,
                namespace=namespace,
                limit=limit,
            )

            results = [
                SearchResult(
                    id=r["id"],
                    statement=r["statement"],
                    certainty=r["certainty"],
                    source=r["source"],
                    valid_at=str(r["valid_at"]) if r["valid_at"] else "",
                )
                for r in result
            ]

        return SearchResponse(results=results)

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
        mode: str = Query("knowledge", regex="^(knowledge|world)$"),
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
        """Detailed metrics and health checks."""
        from .metrics import get_metrics
        from .observability import HealthMonitor

        result: dict = {"metrics": get_metrics().snapshot()}

        if pipeline is not None:
            monitor = HealthMonitor(pipeline.driver, pipeline.redis_client)
            result["health"] = monitor.run_all()

        return result

    return app
