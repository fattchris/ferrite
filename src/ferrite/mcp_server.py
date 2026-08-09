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
    "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
    "mcpServers": {
      "ferrite": {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "--project", "${PLUGIN_ROOT}", "python", "-m", "ferrite.mcp_server"],
        "env": {
          "NEO4J_URI": "bolt://localhost:7687",
          "NEO4J_USER": "neo4j",
          "NEO4J_PASSWORD": "ferrite123",
          "REDIS_URL": "redis://localhost:6379",
          "LITELLM_BASE_URL": "http://localhost:4000/v1",
          "LITELLM_API_KEY": "sk-litellm-...",
          "LLM_MODEL": "glm-5.2"
        }
      }
    }
  }
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

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

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "ferrite123")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "glm-5.2")

# --- Neo4j driver (lazy init) ---

_driver = None
_embedder = None


def _get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase

        _driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _driver


def _get_embedder():
    """Lazy init Ollama embedder for semantic search."""
    global _embedder
    if _embedder is None:
        _embedder = OllamaEmbedder()
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
    with urllib.request.urlopen(req, timeout=120) as r:
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
        name="ferrite_ingest",
        description=(
            "Ingest text content into the knowledge graph. "
            "GLM-5.2 extracts entities and facts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Text content to ingest",
                },
                "source": {
                    "type": "string",
                    "description": "Source identifier (default: 'mcp')",
                    "default": "mcp",
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
]


def _text(text: str) -> list[types.TextContent]:
    """Wrap text as MCP TextContent."""
    return [types.TextContent(type="text", text=text)]


def _json_response(data: Any) -> list[types.TextContent]:
    return _text(json.dumps(data, indent=2, default=str))


# --- Tool handlers ---

def _handle_search(args: dict) -> list[types.TextContent]:
    query = args["query"]
    limit = args.get("limit", 10)
    try:
        embedder = _get_embedder()
    except Exception:
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
    except Exception:
        embedder = None
    results = inject_context(
        _get_driver(), text, _llm_client, embedder=embedder
    )
    return _json_response({"results": results, "count": len(results)})


def _handle_stats(args: dict) -> list[types.TextContent]:
    return _json_response(_get_stats())


def _handle_ingest(args: dict) -> list[types.TextContent]:
    content = args["content"]
    source = args.get("source", "mcp")
    return _json_response(_ingest(content, source))


def _handle_tempr_search(args: dict) -> list[types.TextContent]:
    """TEMPR multi-strategy retrieval."""
    from .tempr import tempr_search

    query = args["query"]
    limit = args.get("limit", 10)
    include_history = args.get("include_history", False)
    try:
        embedder = _get_embedder()
    except Exception:
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
    except Exception:
        r = None

    count = consolidate_pending(_get_driver(), _llm_client, redis_client=r)
    return _json_response({"consolidated_groups": count})


TOOL_HANDLERS = {
    "ferrite_search": _handle_search,
    "ferrite_query": _handle_query,
    "ferrite_entity_facts": _handle_entity_facts,
    "ferrite_multi_hop": _handle_multi_hop,
    "ferrite_inject": _handle_inject,
    "ferrite_stats": _handle_stats,
    "ferrite_ingest": _handle_ingest,
    "ferrite_tempr_search": _handle_tempr_search,
    "ferrite_mental_model": _handle_mental_model,
    "ferrite_consolidate": _handle_consolidate,
}


# --- Server setup ---

server = Server("ferrite")


async def _list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
    """Handle tools/list requests."""
    return types.ListToolsResult(tools=TOOLS)


async def _call_tool(request: types.CallToolRequest) -> types.CallToolResult:
    """Handle tools/call requests."""
    name = request.params.name
    arguments = request.params.arguments or {}

    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return types.CallToolResult(
            content=_text(json.dumps({"error": f"Unknown tool: {name}"})),
            is_error=True,
        )

    try:
        content = handler(arguments)
        return types.CallToolResult(content=content)
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return types.CallToolResult(
            content=_text(json.dumps({"error": str(e)}, indent=2)),
            is_error=True,
        )


server.add_request_handler("tools/list", types.ListToolsRequest, _list_tools)
server.add_request_handler("tools/call", types.CallToolRequest, _call_tool)


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


def _ingest(content: str, source: str) -> dict:
    """Ingest text content into the graph."""
    import uuid as _uuid
    from datetime import datetime

    from .ingestion import IngestionPipeline
    from .models import Episode

    pipe = IngestionPipeline(
        redis_url=REDIS_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
        llm_client=_llm_client,
    )

    ep = Episode(
        id=str(_uuid.uuid4()),
        content=content,
        content_type="text",
        source={"type": "mcp", "name": source},
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

    pipe.close()

    return {
        "episode_id": ep.id,
        "facts_written": fact_count,
    }


# --- Main entry point ---

async def main():
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
