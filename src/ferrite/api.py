"""FastAPI application with Ferrite endpoints.""""

import asyncio
import time
from datetime import datetime
from typing import Optional

import redis.asyncio as aioredis
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from neo4j import Driver

from ferrite.config import get_settings
from ferrite.ingestion import queue_episode, process_episode
from ferrite.schema import init_schema

app = FastAPI(title="Ferrite", version="0.1.0")

_driver: Optional[Driver] = None
_redis: Optional[aioredis.Redis] = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        s = get_settings()
        _driver = GraphDatabase.driver(s.NEO4J_URI, auth=(s.NEO4J_USER, s.NEO4J_PASSWORD))
    return _driver


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(get_settings().REDIS_URL)
    return _redis


# Rate limiting state
_rate_limits: dict[str, dict] = {}
WRITE_METHODS = {"POST"}
ADMIN_KEYS = {"admin", "ferrite-admin"}


def check_rate_limit(api_key: str, is_write: bool) -> bool:
    if api_key in ADMIN_KEYS:
        return True
    limit = 20 if is_write else 100
    window = 60
    now = time.time()
    key = f"{api_key}:{'w' if is_write else 'r'}"
    if key not in _rate_limits:
        _rate_limits[key] = {"count": 0, "reset": now + window}
    state = _rate_limits[key]
    if now > state["reset"]:
        state["count"] = 0
        state["reset"] = now + window
    state["count"] += 1
    return state["count"] <= limit


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    api_key = request.headers.get("X-API-Key", "anonymous")
    is_write = request.method in WRITE_METHODS
    if not check_rate_limit(api_key, is_write):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    return await call_next(request)


@app.on_event("startup")
async def startup_event():
    init_schema(get_driver())
    asyncio.create_task(ingestion_worker())


async def ingestion_worker():
    while True:
        try:
            await process_episode(get_redis(), get_driver())
        except Exception:
            pass
        await asyncio.sleep(0.1)


@app.get("/health")
async def health():
    status = {"status": "healthy"}
    try:
        get_driver().verify_connectivity()
        status["neo4j"] = "connected"
    except Exception:
        status["neo4j"] = "disconnected"
        status["status"] = "degraded"
    try:
        await get_redis().ping()
        status["redis"] = "connected"
    except Exception:
        status["redis"] = "disconnected"
        status["status"] = "degraded"
    try:
        depth = await get_redis().llen("ferrite:ingestion:queue")
        status["queue_depth"] = depth
    except Exception:
        status["queue_depth"] = -1
    return status


@app.post("/store")
async def store(
    request: Request,
    content: str = Query(...),
    content_type: str = Query("text/plain"),
    namespace: Optional[str] = Query(None),
):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    source = body.get("source", {"type": "api"})
    return await queue_episode(get_redis(), content, content_type, source, namespace)


@app.get("/search")
async def search(
    query: str = Query(...),
    namespace: Optional[str] = Query(None),
    limit: int = Query(10, le=50),
):
    ns = namespace or get_settings().NAMESPACE_DEFAULT
    cypher = """
    CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $query) YIELD node, score
    WHERE node:Fact AND node.namespace = $namespace
    RETURN node.id AS id, node.statement AS statement, node.certainty AS certainty,
           node.valid_at AS valid_at, node.epistemic_state AS epistemic_state, score
    ORDER BY score DESC
    LIMIT $limit
    """
    with get_driver().session() as session:
        result = session.run(cypher, query=query, namespace=ns, limit=limit)
        return [dict(r) for r in result]


@app.get("/entity/{entity_id}")
async def get_entity(entity_id: str, namespace: Optional[str] = Query(None)):
    ns = namespace or get_settings().NAMESPACE_DEFAULT
    query = """
    MATCH (e:Entity {id: $entity_id})
    OPTIONAL MATCH (e)<-[:SUBJECT]-(f:Fact {namespace: $namespace})
    RETURN e.id AS id, e.name AS name, e.type AS type, e.summary AS summary,
           collect({
               id: f.id, statement: f.statement, predicate: f.predicate,
               certainty: f.certainty, epistemic_state: f.epistemic_state,
               valid_at: f.valid_at, invalid_at: f.invalid_at
           }) AS facts
    """
    with get_driver().session() as session:
        rec = session.run(query, entity_id=entity_id, namespace=ns).single()
        if not rec:
            raise HTTPException(status_code=404, detail="Entity not found")
        return dict(rec)


@app.get("/history/{fact_id}")
async def get_history(
    fact_id: str,
    at_time: Optional[datetime] = Query(None),
    mode: str = Query("knowledge"),
    namespace: Optional[str] = Query(None),
):
    ns = namespace or get_settings().NAMESPACE_DEFAULT

    if mode == "knowledge":
        # as_of_knowledge: recorded_at <= T
        query = """
        MATCH (f:Fact {id: $fact_id, namespace: $namespace})
        WHERE f.recorded_at <= $at_time
        RETURN f
        """
    elif mode == "world":
        # as_of_world: valid_at <= T < coalesce(invalid_at, ∞)
        query = """
        MATCH (f:Fact {id: $fact_id, namespace: $namespace})
        WHERE f.valid_at <= $at_time AND (f.invalid_at IS NULL OR f.invalid_at > $at_time)
        RETURN f
        """
    else:
        raise HTTPException(status_code=400, detail="Invalid mode")

    with get_driver().session() as session:
        rec = session.run(query, fact_id=fact_id, namespace=ns, at_time=at_time or datetime.now()).single()
        if not rec:
            raise HTTPException(status_code=404, detail="Fact not found for given time")
        return dict(rec)
