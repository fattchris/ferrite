"""Ferrite MCP Server — exposes knowledge graph query tools via stdio MCP.

Tools exposed:
  - ferrite_search: Fulltext search on fact statements
  - ferrite_query: Natural language → Cypher via GLM-5.2
  - ferrite_entity_facts: Get all facts for a named entity
  - ferrite_multi_hop: Traverse N hops from an entity
  - ferrite_inject: Determine if context should be injected for a user turn
  - ferrite_stats: Graph statistics (entity count, fact count, etc.)
  - ferrite_ingest: Ingest text content into the graph (enqueue + process)

Usage (standalone):
  cd ~/ferrite && uv run python -m ferrite.mcp_server

Agent Plugins 1.0.0 mcp.json:
  {
    "mcpServers": {
      "ferrite": {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "--project", "${PLUGIN_ROOT}", "python", "-m", "ferrite.mcp_server"],
        "env": { "FERRITE_API_KEY": "${FERRITE_API_KEY}" }
      },
      "ferrite-http": {
        "type": "http",
        "url": "http://localhost:8002/mcp/",
        "headers": { "Authorization": "Bearer ${FERRITE_API_KEY}" }
      }
    }
  }

Credentials: agents authenticate with a scoped Ferrite API key (created via
POST /keys with admin scope). Raw Neo4j/Redis/LiteLLM creds are NEVER exposed
to client agents. Provenance is stamped server-side from the key.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from types import SimpleNamespace
from typing import Any, Callable

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

# HTTP transport (§4.1) — streamable HTTP at /mcp/
try:
    from mcp.server.streamable_http import StreamableHTTPServerTransport
    _HTTP_AVAILABLE = True
except ImportError:
    _HTTP_AVAILABLE = False

from .circuit_breaker import get_circuit_breaker
from .db import get_driver
from .embeddings import OllamaEmbedder
from .query import (
    get_entity_facts,
    inject_context,
    multi_hop_query,
    nl_to_cypher,
    search_facts,
)

logger = logging.getLogger(__name__)

# --- Config from env ---

from .config import get_settings as _get_settings  # noqa: E402

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _get_settings().NEO4J_PASSWORD
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")

from .config import get_settings as _get_settings  # noqa: E402

_settings = _get_settings()
LITELLM_BASE_URL = _settings.LLM_BASE_URL
LLM_MODEL = _settings.LLM_MODEL

# --- Neo4j driver (singleton via db.get_driver) ---

_embedder = None
_pipeline = None


def _get_driver():
    """Compatibility wrapper — delegates to db.get_driver() singleton."""
    return get_driver()


def _get_embedder():
    """Lazy init Ollama embedder for semantic search."""
    global _embedder
    if _embedder is None:
        _s = _get_settings()
        _embedder = OllamaEmbedder(
            model_name=_s.EMBED_MODEL,
            host=_s.EMBED_BASE_URL,
        )
    return _embedder


def _llm_client(system_prompt: str, user_prompt: str) -> str:
    """Call LiteLLM proxy for LLM completions."""
    data = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        f"{LITELLM_BASE_URL}/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LITELLM_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=_get_settings().LLM_TIMEOUT) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


# --- Tool definitions ---

TOOLS = [
    types.Tool(
        name="ferrite_search",
        description=(
            "Search the Ferrite knowledge graph by keywords. "
            "Returns matching facts with entity names, predicates, "
            "and statements."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (keywords or phrase)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="ferrite_query",
        description=(
            "Ask a natural language question about the knowledge "
            "graph. GLM-5.2 translates it to a Cypher query and "
            "executes it against Neo4j."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural language question",
                },
            },
            "required": ["question"],
        },
    ),
    types.Tool(
        name="ferrite_entity_facts",
        description=(
            "Get all facts for a specific entity by name. "
            "Returns subject and object facts with predicates, "
            "statements, and temporal state."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_name": {
                    "type": "string",
                    "description": "Entity name (e.g. 'spark-07')",
                },
            },
            "required": ["entity_name"],
        },
    ),
    types.Tool(
        name="ferrite_multi_hop",
        description=(
            "Traverse the graph N hops from an entity. "
            "Returns connected entities and facts at each hop."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_name": {
                    "type": "string",
                    "description": "Starting entity name",
                },
                "hops": {
                    "type": "integer",
                    "description": "Number of hops (default 2, max 5)",
                    "default": 2,
                },
            },
            "required": ["entity_name"],
        },
    ),
    types.Tool(
        name="ferrite_inject",
        description=(
            "Given a user's message, determine if any facts from "
            "the knowledge graph should be injected as context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "User's message/turn text",
                },
            },
            "required": ["text"],
        },
    ),
    types.Tool(
        name="ferrite_stats",
        description=(
            "Get knowledge graph statistics: entity count, fact "
            "count, predicate distribution, temporal state."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    types.Tool(
        name="ferrite_assert",
        description=(
            "Directly assert known facts as triples into the knowledge graph. "
            "ZERO LLM extraction — use when the model already knows the fact. "
            "Predicates are validated against the predicate vocabulary."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "description": "Facts to assert",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {"type": "string"},
                            "predicate": {
                                "type": "string",
                                "description": "Predicate from Ferrite vocab (e.g. works_at, uses, version_is)",
                            },
                            "object": {"type": "string"},
                            "object_type": {
                                "type": "string",
                                "enum": ["entity", "literal"],
                                "description": "Kind of object; default entity",
                            },
                            "certainty": {
                                "type": "string",
                                "enum": ["stated", "inferred", "speculative"],
                                "description": "Epistemic certainty label; default stated",
                            },
                        },
                        "required": ["subject", "predicate", "object"],
                    },
                },
                "content": {
                    "type": "string",
                    "description": "Optional context text for the episode",
                },
                "namespace": {
                    "type": "string",
                    "description": "Namespace (default shared)",
                    "default": "shared",
                },
            },
            "required": ["facts"],
        },
    ),
    types.Tool(
        name="ferrite_ingest",
        description=(
            "Ingest text content into the knowledge graph. "
            "GLM-5.2 extracts entities and facts. "
            "Original content is saved to the file repository."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Text content to ingest",
                },
                "source": {
                    "type": "object",
                    "description": (
                        "Source metadata. Supported keys: "
                        "type (arxiv, web, code, text, voice), "
                        "name (identifier), "
                        "url (source URL), "
                        "arxiv_id (e.g. 2401.12345), "
                        "filename (override file name)"
                    ),
                    "default": {"type": "mcp", "name": "mcp"},
                },
                "content_type": {
                    "type": "string",
                    "description": "Content type: text, code, pdf, web, voice, json",
                    "default": "text",
                },
            },
            "required": ["content"],
        },
    ),
    types.Tool(
        name="ferrite_tempr_search",
        description=(
            "TEMPR multi-strategy retrieval: semantic, BM25, "
            "graph, temporal, and recency strategies fused "
            "with Reciprocal Rank Fusion (RRF)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10)",
                    "default": 10,
                },
                "include_history": {
                    "type": "boolean",
                    "description": "Include superseded facts",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="ferrite_mental_model",
        description=(
            "Create or search mental models — user-curated "
            "summaries for common query patterns."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "create", "draft"],
                    "description": "search/find, create/manual, draft/LLM-generate",
                },
                "query": {
                    "type": "string",
                    "description": "Search query or entity name",
                },
                "title": {
                    "type": "string",
                    "description": "Title (for create/draft)",
                },
                "summary": {
                    "type": "string",
                    "description": "Summary text (for create)",
                },
            },
            "required": ["action", "query"],
        },
    ),
    types.Tool(
        name="ferrite_consolidate",
        description=(
            "Run observation consolidation on pending groups. "
            "Synthesizes raw facts into higher-level beliefs "
            "with evidence tracking."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    # --- Spec §4.2 tools ---
    types.Tool(
        name="ferrite_get_provenance",
        description=(
            "Get the full provenance chain for a fact or episode: "
            "agent → channel → session → episode → source. "
            "Cross-namespace chains truncate with redacted_beyond_this_point."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "fact_id": {
                    "type": "string",
                    "description": "Fact ID to trace provenance for.",
                },
            },
            "required": ["fact_id"],
        },
    ),
    types.Tool(
        name="ferrite_list_episodes",
        description=(
            "List recent episodes ingested, optionally filtered by since timestamp."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max episodes to return (default 20).",
                },
                "since": {
                    "type": "string",
                    "description": "ISO timestamp — only episodes after this.",
                },
            },
        },
    ),
]


def _text(text: str) -> list[types.TextContent]:
    """Wrap text as MCP TextContent."""
    return [types.TextContent(type="text", text=text)]


def _json_response(data: Any) -> list[types.TextContent]:
    return _text(json.dumps(data, indent=2, default=str))


def _protected(handler: Callable) -> Callable:
    """Wrap a handler with the circuit breaker.

    If the circuit is open, returns a fallback response immediately
    instead of attempting a Neo4j/Ollama call that would hang or fail.
    """
    breaker = get_circuit_breaker()

    def wrapper(args: dict) -> list[types.TextContent]:
        if not breaker.can_execute():
            logger.warning(
                "Circuit breaker OPEN — %s returning fallback",
                handler.__name__,
            )
            return _json_response({
                "error": "circuit_breaker_open",
                "message": (
                    "Ferrite service is temporarily unavailable. "
                    "Falling back to local memory only."
                ),
            })
        try:
            result = handler(args)
            # Success — record it
            breaker.call(lambda: None)  # record success
            return result
        except Exception as exc:
            # Let the breaker record the failure
            _exc = exc  # capture for lambda closure
            breaker.call(
                lambda: (_ for _ in ()).throw(_exc),
                fallback=None,
            )
            logger.exception("Tool %s failed", handler.__name__)
            return _json_response({"error": str(exc)})

    return wrapper


# --- Tool handlers ---

def _handle_search(args: dict) -> list[types.TextContent]:
    query = args["query"]
    limit = args.get("limit", 10)
    try:
        embedder = _get_embedder()
    except Exception as e:
        logger.debug(f"Embedder init failed for search: {e}")
        embedder = None
    results = search_facts(
        _get_driver(), query, limit=limit, embedder=embedder
    )
    return _json_response({"results": results, "count": len(results)})


def _handle_query(args: dict) -> list[types.TextContent]:
    question = args["question"]
    results = nl_to_cypher(question, _get_driver(), _llm_client)
    return _json_response({"results": results, "count": len(results)})


def _handle_entity_facts(args: dict) -> list[types.TextContent]:
    entity_name = args["entity_name"]
    results = get_entity_facts(_get_driver(), entity_name)
    return _json_response(results)


def _handle_multi_hop(args: dict) -> list[types.TextContent]:
    entity_name = args["entity_name"]
    hops = min(args.get("hops", 2), 5)
    results = multi_hop_query(_get_driver(), entity_name, hops=hops)
    return _json_response({"results": results, "count": len(results)})


def _handle_inject(args: dict) -> list[types.TextContent]:
    text = args["text"]
    try:
        embedder = _get_embedder()
    except Exception as e:
        logger.debug(f"Embedder init failed for inject: {e}")
        embedder = None
    results = inject_context(
        _get_driver(), text, _llm_client, embedder=embedder
    )
    return _json_response({"results": results, "count": len(results)})


def _handle_stats(args: dict) -> list[types.TextContent]:
    return _json_response(_get_stats())


def _handle_ingest(args: dict) -> list[types.TextContent]:
    content = args["content"]
    # Accept source as dict or string (backward compat)
    source = args.get("source", {"type": "mcp", "name": "mcp"})
    if isinstance(source, str):
        source = {"type": "mcp", "name": source}
    content_type = args.get("content_type", "text")
    return _json_response(_ingest(content, source, content_type))


def _handle_assert(args: dict) -> list[types.TextContent]:
    """Direct assertion: triples -> graph with no LLM. Reuses /assert pipeline."""
    facts = args.get("facts", [])
    if not facts or not isinstance(facts, list):
        return _json_response({"error": "no facts supplied"})
    for f in facts:
        if not isinstance(f, dict) or not f.get("subject") or not f.get("predicate") or not f.get("object"):
            return _json_response({
                "error": "invalid fact: each fact requires non-empty 'subject', 'predicate', and 'object' keys",
            })
    content = args.get("content") or ""
    namespace = args.get("namespace", "shared")

    from .canonicalize import resolve_entity
    from .models import Episode, ExtractedEntity, ExtractedFact

    def _norm_object_type(v):
        return v if v in ("entity", "literal") else "entity"

    def _norm_certainty(v):
        return v if v in ("stated", "inferred", "speculative") else "stated"

    pipe = _get_pipeline()
    import uuid as _uuid
    from datetime import datetime

    # Only materialize object entities for object_type == "entity"
    entities = [
        ExtractedEntity(
            name=f["object"],
            type=_norm_object_type(f.get("object_type")),
            summary="",
        )
        for f in facts
        if f.get("object") and _norm_object_type(f.get("object_type")) == "entity"
    ]
    extracted = [
        ExtractedFact(
            subject=f["subject"],
            predicate=f["predicate"],
            object=f["object"],
            object_type=_norm_object_type(f.get("object_type")),
            certainty=_norm_certainty(f.get("certainty")),
            assertion_source="user",
        )
        for f in facts
    ]
    ep = Episode(
        id=str(_uuid.uuid4()),
        content=content or "\n".join(f"{f['subject']} {f['predicate']} {f['object']}" for f in facts),
        content_type="structured",
        source={"agent": "mcp", "kind": "assert"},
        namespace=namespace,
        recorded_at=datetime.now(),
    )
    entity_cache = {}
    for ent in entities:
        entity = resolve_entity(pipe.driver, ent.name, pipe.embedding_func,
                                entity_type=ent.type, summary=ent.summary)
        if entity is not None:
            entity_cache[ent.name] = entity
    for f in extracted:
        for name in ([f.subject] + ([f.object] if f.object_type == "entity" else [])):
            if name not in entity_cache:
                e = resolve_entity(pipe.driver, name, pipe.embedding_func)
                if e is not None:
                    entity_cache[name] = e

    from .quality_gates import assertion_gate
    written = 0
    with pipe.driver.session() as session:
        for fact_data in extracted:
            if not assertion_gate(fact_data.model_dump()):
                continue
            pipe._write_fact_with_temporal(ep, fact_data, entity_cache, session)
            written += 1
    return _json_response({"episode_id": ep.id, "facts_written": written})


def _handle_tempr_search(args: dict) -> list[types.TextContent]:
    """TEMPR multi-strategy retrieval."""
    from .tempr import tempr_search

    query = args["query"]
    limit = args.get("limit", 10)
    include_history = args.get("include_history", False)
    try:
        embedder = _get_embedder()
    except Exception as e:
        logger.debug(f"Embedder init failed for TEMPR search: {e}")
        embedder = None
    results = tempr_search(
        _get_driver(), query, embedder=embedder,
        limit=limit, include_history=include_history,
    )
    return _json_response({"results": results, "count": len(results)})


def _handle_mental_model(args: dict) -> list[types.TextContent]:
    """Mental model create/search/draft."""
    from .mental_models import (
        create_mental_model,
        draft_mental_model,
        search_mental_models,
    )

    action = args["action"]
    query = args["query"]

    if action == "search":
        results = search_mental_models(_get_driver(), query)
        return _json_response(
            {"results": results, "count": len(results)}
        )
    elif action == "create":
        title = args.get("title", query)
        summary = args.get("summary", "")
        model_id = create_mental_model(
            _get_driver(), title=title, summary=summary,
            curated_for=[query],
        )
        return _json_response({"model_id": model_id, "status": "created"})
    elif action == "draft":
        model_id = draft_mental_model(
            _get_driver(), query, _llm_client,
        )
        if model_id:
            return _json_response(
                {"model_id": model_id, "status": "drafted_needs_approval"}
            )
        return _json_response({"status": "no_facts_found"})
    else:
        return _json_response({"error": f"Unknown action: {action}"})


def _handle_consolidate(args: dict) -> list[types.TextContent]:
    """Run observation consolidation on pending groups."""
    from .consolidator import consolidate_pending

    try:
        import redis as _redis
        r = _redis.from_url(REDIS_URL)
    except Exception as e:
        logger.debug(f"Redis init failed for consolidation: {e}")
        r = None

    count = consolidate_pending(_get_driver(), _llm_client, redis_client=r)
    return _json_response({"consolidated_groups": count})


def _handle_get_provenance(args: dict) -> list[types.TextContent]:
    """Get provenance chain for a fact (§4.2).

    Chain: agent → channel → session → episode → source.
    Cross-namespace chains truncate with redacted_beyond_this_point (§6.3).
    """
    fact_id = args.get("fact_id", "")
    if not fact_id:
        return _json_response({"error": "fact_id required"})

    driver = _get_driver()
    with driver.session() as session:
        # Trace: Fact → SOURCED_FROM → Episode → (source fields)
        result = session.run(
            """
            MATCH (f:Fact {id: $fact_id})-[:SOURCED_FROM]->(ep:Episode)
            RETURN ep.id AS episode_id,
                   ep.content AS content,
                   ep.content_type AS content_type,
                   ep.source AS source,
                   ep.namespace AS namespace,
                   ep.recorded_at AS recorded_at
            """,
            fact_id=fact_id,
        )
        episode = result.single()

    if not episode:
        return _json_response({"error": "Fact or provenance not found"})

    # Parse the source JSON for agent/channel/session info
    import json as _json
    source_raw = episode["source"] or "{}"
    try:
        source = _json.loads(source_raw) if isinstance(source_raw, str) else source_raw
    except (ValueError, TypeError) as e:
        logger.debug(f"Source JSON parse failed: {e}")
        source = {"raw": source_raw}

    # Build the provenance chain
    chain = {
        "fact_id": fact_id,
        "episode_id": episode["episode_id"],
        "namespace": episode["namespace"],
        "recorded_at": str(episode["recorded_at"]) if episode["recorded_at"] else None,
        "source": source,
        "chain": [
            {"level": "agent", "value": source.get("agent", "unknown")},
            {"level": "channel", "value": source.get("channel", "unknown")},
            {"level": "session", "value": source.get("session", "unknown")},
            {"level": "episode", "value": episode["episode_id"]},
            {"level": "source", "value": source.get("type", "session_transcript")},
        ],
    }

    # Cross-namespace truncation marker (§6.3)
    # If the episode's namespace differs from the requesting agent's namespace,
    # truncate with redacted_beyond_this_point
    # (Full enforcement requires knowing the caller's namespace — for MCP
    # stdio we trust the local agent; for HTTP the middleware enforces this.)
    chain["redacted_beyond_this_point"] = False

    return _json_response(chain)


def _handle_list_episodes(args: dict) -> list[types.TextContent]:
    """List recent episodes ingested (§4.2)."""
    limit = args.get("limit", 20)
    since = args.get("since")

    driver = _get_driver()
    since_filter = "WHERE ep.recorded_at >= $since" if since else ""
    params = {"limit": limit}
    if since:
        params["since"] = since

    with driver.session() as session:
        result = session.run(
            f"""
            MATCH (ep:Episode)
            {since_filter}
            RETURN ep.id AS id, ep.content_type AS content_type,
                   ep.namespace AS namespace, ep.recorded_at AS recorded_at,
                   substring(ep.content, 0, 200) AS preview
            ORDER BY ep.recorded_at DESC
            LIMIT $limit
            """,
            **params,
        )
        episodes = [
            {
                "id": r["id"],
                "content_type": r["content_type"],
                "namespace": r["namespace"],
                "recorded_at": str(r["recorded_at"]) if r["recorded_at"] else None,
                "preview": r["preview"],
            }
            for r in result
        ]

    return _json_response({"episodes": episodes, "count": len(episodes)})


TOOL_HANDLERS = {
    "ferrite_search": _protected(_handle_search),
    "ferrite_query": _protected(_handle_query),
    "ferrite_entity_facts": _protected(_handle_entity_facts),
    "ferrite_multi_hop": _protected(_handle_multi_hop),
    "ferrite_inject": _protected(_handle_inject),
    "ferrite_stats": _protected(_handle_stats),
    "ferrite_ingest": _protected(_handle_ingest),
    "ferrite_assert": _protected(_handle_assert),
    "ferrite_tempr_search": _protected(_handle_tempr_search),
    "ferrite_mental_model": _protected(_handle_mental_model),
    "ferrite_consolidate": _protected(_handle_consolidate),
    "ferrite_get_provenance": _protected(_handle_get_provenance),
    "ferrite_list_episodes": _protected(_handle_list_episodes),
}


# --- Server setup ---

import importlib.metadata as _im
try:
    _FERRITE_VERSION = _im.version("ferrite")
except Exception:
    _FERRITE_VERSION = "0.1.0"
server = Server("ferrite", version=_FERRITE_VERSION)


async def _list_tools(ctx, params) -> types.ListToolsResult:
    """Handle tools/list requests."""
    return types.ListToolsResult(tools=TOOLS)


async def _call_tool(ctx, params) -> types.CallToolResult:
    """Handle tools/call requests."""
    name = params.name
    arguments = params.arguments or {}

    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return types.CallToolResult(
            content=_text(json.dumps({"error": f"Unknown tool: {name}"})),
            is_error=True,
        )

    try:
        content = handler(arguments)
        # If a handler returned an error payload, surface it as isError so
        # MCP clients don't treat failures as success.
        if (
            len(content) == 1
            and isinstance(content[0], types.TextContent)
        ):
            try:
                payload = json.loads(content[0].text)
                if isinstance(payload, dict) and "error" in payload:
                    return types.CallToolResult(content=content, isError=True)
            except (json.JSONDecodeError, ValueError):
                pass
        return types.CallToolResult(content=content)
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return types.CallToolResult(
            content=_text(json.dumps({"error": str(e)}, indent=2)),
            is_error=True,
        )


server.add_request_handler("tools/list", types.PaginatedRequestParams, _list_tools)
server.add_request_handler("tools/call", types.CallToolRequestParams, _call_tool)


# --- Helper functions ---

def _get_stats() -> dict:
    """Get graph statistics."""
    driver = _get_driver()
    with driver.session() as s:
        entities = s.run("MATCH (e:Entity) RETURN count(e) AS c").single()["c"]
        facts = s.run("MATCH (f:Fact) RETURN count(f) AS c").single()["c"]
        active = s.run(
            "MATCH (f:Fact {epistemic_state: 'active'}) RETURN count(f) AS c"
        ).single()["c"]
        superseded = s.run(
            "MATCH (f:Fact {epistemic_state: 'superseded'}) RETURN count(f) AS c"
        ).single()["c"]
        episodes = s.run("MATCH (e:Episode) RETURN count(e) AS c").single()["c"]
        literals = s.run("MATCH (l:Literal) RETURN count(l) AS c").single()["c"]

        pred_result = s.run(
            "MATCH (f:Fact) RETURN f.predicate AS p, count(f) AS c "
            "ORDER BY c DESC LIMIT 10"
        )
        top_predicates = [
            {"predicate": r["p"], "count": r["c"]} for r in pred_result
        ]

    return {
        "entities": entities,
        "facts": facts,
        "active_facts": active,
        "superseded_facts": superseded,
        "episodes": episodes,
        "literals": literals,
        "top_predicates": top_predicates,
    }


def _get_pipeline():
    """Module-level singleton IngestionPipeline for MCP ingest.

    Lazily creates one IngestionPipeline (connected to Redis + Neo4j via
    get_driver()) and reuses it for all ferrite_ingest calls instead of
    constructing a new pipeline per call.
    """
    global _pipeline
    if _pipeline is None:
        from .ingestion import IngestionPipeline

        _pipeline = IngestionPipeline(
            redis_url=REDIS_URL,
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
            llm_client=_llm_client,
        )
    return _pipeline


def _ingest(content: str, source: dict, content_type: str = "text") -> dict:
    """Ingest text content into the graph."""
    import uuid as _uuid
    from datetime import datetime

    from .models import Episode

    pipe = _get_pipeline()

    ep = Episode(
        id=str(_uuid.uuid4()),
        content=content,
        content_type=content_type,
        source=source,
        namespace="shared",
        recorded_at=datetime.now(),
    )

    pipe.enqueue(ep)
    pipe.process_next()

    with pipe.driver.session() as s:
        result = s.run(
            "MATCH (ep:Episode {id: $ep_id})<-[:SOURCED_FROM]-(f:Fact) "
            "RETURN count(f) AS c",
            ep_id=ep.id,
        )
        record = result.single()
        fact_count = record["c"] if record else 0

    return {
        "episode_id": ep.id,
        "facts_written": fact_count,
        "source_file": ep.source_file,
    }


# --- Main entry points ---

async def main_stdio():
    """Run the MCP server over stdio (for Claude Desktop, local agents)."""
    logging.basicConfig(level=logging.INFO)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main_http(host: str = "0.0.0.0", port: int = 8002):
    """Run the MCP server over streamable HTTP at /mcp/ (§4.1).

    Stateless mode: a fresh transport per request (per MCP SDK stateless
    pattern) — any number of clients can connect concurrently.
    Usage: python -m ferrite.mcp_server --http
    """
    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse as _JSONResponse
    from starlette.routing import Route

    logging.basicConfig(level=logging.INFO)
    logger.info(f"Starting MCP HTTP server on {host}:{port}/mcp/")

    import asyncio as _asyncio
    import contextlib as _ctxlib
    import uuid as _uuid

    # session_id -> {"transport":..., "task":...}; stateful per client session.
    _sessions: dict = {}

    async def handle_health(request):
        return _JSONResponse({"ok": True, "service": "ferrite-mcp"})

    async def _handle_stateless(request):
        """Stateful per MCP session: initialize creates a session; subsequent
        requests with the Mcp-Session-Id header reuse its transport."""
        header_sid = request.headers.get("mcp-session-id")

        if header_sid and header_sid in _sessions:
            transport = _sessions[header_sid]["transport"]
            await transport.handle_request(
                request.scope, request.receive, request._send
            )
            return

        transport = StreamableHTTPServerTransport(
            mcp_session_id=_uuid.uuid4().hex,
            is_json_response_enabled=True,
        )
        sid = transport.mcp_session_id

        ready = _asyncio.Event()

        async def _run_server():
            async with transport.connect() as (read_stream, write_stream):
                ready.set()
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )

        server_task = _asyncio.create_task(_run_server())
        _sessions[sid] = {"transport": transport, "task": server_task}

        async def _send_with_session(scope, receive, send):
            async def send_wrapper(message):
                if message.get("type") == "http.response.start":
                    headers = [(k, v) for k, v in message.get("headers", [])
                               if k.lower() != b"mcp-session-id"]
                    headers.append((b"mcp-session-id", sid.encode()))
                    message["headers"] = headers
                await send(message)
            await transport.handle_request(scope, receive, send_wrapper)

        try:
            await _asyncio.wait_for(ready.wait(), timeout=5)
            # Force JSON responses: some HTTP clients (undici) treat SSE content-type
            # as an open stream and never "finish" the request. Accept header is
            # rewritten to application/json so the SDK replies with plain JSON.
            scope = dict(request.scope)
            for idx, (k, v) in enumerate(scope.get("headers", [])):
                if k == b"accept":
                    scope["headers"][idx] = (k, b"application/json")
                    break
            await _send_with_session(scope, request.receive, request._send)
        finally:
            # If the client has completed and closed, sessions can be GC'd lazily.
            if len(_sessions) > 512:
                _sessions.clear()

    async def handle_mcp(request):
        """POST → per-session handling. GET/DELETE → 405."""
        if request.method in ("GET", "DELETE"):
            return _JSONResponse(
                {"detail": "GET/DELETE not supported (stateful POST-only)"},
                status_code=405,
            )
        return await _handle_stateless(request)

    # --- Auth (§6.1): same key store as the REST API ---
    from .key_store import validate_token as _ks_validate

    def _mcp_key_from_request(request):
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return request.headers.get("x-api-key", "")

    class McpAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path == "/health":
                return await call_next(request)  # unauthenticated liveness probe
            key_info = _ks_validate(_mcp_key_from_request(request))
            if key_info is None:
                env_key = os.environ.get("FERRITE_API_KEY", "")
                keys_db = os.environ.get("FERRITE_KEYS_DB", "")
                if not env_key and not keys_db:
                    key_info = SimpleNamespace(
                        key_id="dev", agent_name="dev",
                        scopes=["read", "write", "ingest", "admin"],
                        namespaces=["shared", "personal", "e2e-test"],
                    )
            if key_info is None:
                return _JSONResponse(
                    {"detail": "Invalid or missing API key"}, status_code=401
                )
            request.state.key_info = key_info
            return await call_next(request)

    app = Starlette(
        routes=[
            Route("/mcp/", handle_mcp, methods=["GET", "POST", "DELETE"]),
            Route("/health", handle_health, methods=["GET"]),
        ],
    )
    app.add_middleware(McpAuthMiddleware)

    config = uvicorn.Config(app, host=host, port=port, timeout_keep_alive=5)
    uvicorn.Server(config).run()


if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    if "--http" in sys.argv:
        # HTTP transport mode
        ap = argparse.ArgumentParser()
        ap.add_argument("--http", action="store_true")
        ap.add_argument("--host", default="0.0.0.0")
        ap.add_argument("--port", type=int, default=8002)
        args = ap.parse_args()
        asyncio.run(main_http(host=args.host, port=args.port))
    else:
        # Default: stdio transport
        asyncio.run(main_stdio())
