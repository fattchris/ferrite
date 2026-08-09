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

Hermes config.yaml:
  mcp_servers:
    ferrite:
      command: "uv"
      args: ["run", "--project", "/Users/fontes/ferrite", "python", "-m", "ferrite.mcp_server"]
      env:
        LITELLM_API_KEY: "sk-litellm-..."
        LITELLM_BASE_URL: "http://localhost:4000/v1"
        NEO4J_URI: "bolt://localhost:7687"
        NEO4J_USER: "neo4j"
        NEO4J_PASSWORD: "ferrite123"
        REDIS_URL: "redis://localhost:6379"
"""

import json
import logging
import os
import urllib.request
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

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


def _get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        _driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _driver


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
        f"{LITELLM_BASE_URL}/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LITELLM_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


# --- MCP server ---

server = Server("ferrite")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="ferrite_search",
            description="Search the Ferrite knowledge graph by keywords. "
                        "Returns matching facts with entity names, predicates, "
                        "and statements.",
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
            description="Ask a natural language question about the knowledge "
                        "graph. GLM-5.2 translates it to a Cypher query and "
                        "executes it against Neo4j. Example: 'What model runs "
                        "on Spark 07?'",
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
            description="Get all facts for a specific entity by name. "
                        "Returns subject and object facts with predicates, "
                        "statements, and temporal state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_name": {
                        "type": "string",
                        "description": "Entity name (e.g. 'spark-07', 'glm-5.2')",
                    },
                },
                "required": ["entity_name"],
            },
        ),
        types.Tool(
            name="ferrite_multi_hop",
            description="Traverse the graph N hops from an entity. "
                        "Returns connected entities and facts at each hop.",
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
            description="Given a user's message, determine if any facts from "
                        "the knowledge graph should be injected as context. "
                        "Returns relevant facts or empty if nothing relevant "
                        "(silence floor).",
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
            description="Get knowledge graph statistics: entity count, fact "
                        "count, predicate distribution, temporal state breakdown.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="ferrite_ingest",
            description="Ingest text content into the knowledge graph. "
                        "GLM-5.2 extracts entities and facts, which are "
                        "canonicalized and written to Neo4j with temporal logic. "
                        "Returns the extracted entities and facts count.",
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
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    """Execute a Ferrite tool and return results as text."""
    if arguments is None:
        arguments = {}

    try:
        if name == "ferrite_search":
            query = arguments["query"]
            limit = arguments.get("limit", 10)
            results = search_facts(_get_driver(), query, limit=limit)
            return [types.TextContent(
        type="text",
        text=json.dumps({"results": results, "count": len(results)}, indent=2)
            )]

        elif name == "ferrite_query":
            question = arguments["question"]
            results = nl_to_cypher(question, _get_driver(), _llm_client)
            return [types.TextContent(
        type="text",
        text=json.dumps({"results": results, "count": len(results)}, indent=2)
            )]

        elif name == "ferrite_entity_facts":
            entity_name = arguments["entity_name"]
            results = get_entity_facts(_get_driver(), entity_name)
            return [types.TextContent(
        type="text",
        text=json.dumps(results, indent=2)
            )]

        elif name == "ferrite_multi_hop":
            entity_name = arguments["entity_name"]
            hops = min(arguments.get("hops", 2), 5)
            results = multi_hop_query(_get_driver(), entity_name, hops=hops)
            return [types.TextContent(
        type="text",
        text=json.dumps({"results": results, "count": len(results)}, indent=2)
            )]

        elif name == "ferrite_inject":
            text = arguments["text"]
            results = inject_context(_get_driver(), text, _llm_client)
            return [types.TextContent(
        type="text",
        text=json.dumps({"results": results, "count": len(results)}, indent=2)
            )]

        elif name == "ferrite_stats":
            stats = _get_stats()
            return [types.TextContent(
        type="text",
        text=json.dumps(stats, indent=2)
            )]

        elif name == "ferrite_ingest":
            content = arguments["content"]
            source = arguments.get("source", "mcp")
            result = _ingest(content, source)
            return [types.TextContent(
        type="text",
        text=json.dumps(result, indent=2)
            )]

        else:
            return [types.TextContent(
        type="text",
        text=json.dumps({"error": f"Unknown tool: {name}"})
            )]

    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e)}, indent=2)
        )]


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

        # Top predicates
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
    processed = pipe.process_next()

    # Count resulting facts
    with pipe.driver.session() as s:
        result = s.run(
            "MATCH (ep:Episode {id: $ep_id})<-[:SOURCED_FROM]-(f:Fact) "
            "RETURN count(f) AS c",
            ep_id=ep.id,
        )
        fact_count = result.single()["c"]

    pipe.close()

    return {
        "episode_id": ep.id,
        "processed": processed is not None,
        "facts_written": fact_count,
    }


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
