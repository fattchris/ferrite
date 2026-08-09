---
name: ferrite-query
description: Search and query the Ferrite temporal knowledge graph for facts about infrastructure, people, projects, and history.
---

# Ferrite Knowledge Graph Query

Use when you need to recall facts about the user's infrastructure, projects, preferences, or history that may be stored in the temporal knowledge graph.

## Available MCP Tools

### `ferrite_search`
Search the graph by keywords. Returns matching facts with entity names, predicates, and statements.
- **query** (required): Keywords or phrase to search for
- **limit** (optional): Max results (default 10)

### `ferrite_query`
Ask a natural language question. GLM-5.2 translates it to Cypher and executes it.
- **question** (required): Natural language question (e.g., "What model runs on Spark 07?")

### `ferrite_entity_facts`
Get all facts for a specific entity by name.
- **entity_name** (required): Entity name (e.g., "spark-07", "glm-5.2", "chris")

### `ferrite_multi_hop`
Traverse N hops from an entity. Returns connected entities and facts at each hop.
- **entity_name** (required): Starting entity name
- **hops** (optional): Number of hops (default 2, max 5)

### `ferrite_inject`
Given a user's message, determine if any facts should be injected as context.
- **text** (required): User's message/turn text

### `ferrite_stats`
Get graph statistics: entity count, fact count, predicate distribution, temporal state.

### `ferrite_ingest`
Ingest text content into the graph. LLM extracts entities and facts automatically.
- **content** (required): Text to ingest
- **source** (optional): Source identifier (default "mcp")

## When to Use

- Before answering questions about infrastructure, deployments, or projects
- When you need to find relationships between entities (multi-hop)
- To check temporal state (active vs superseded facts)
- To store new durable facts for future recall
