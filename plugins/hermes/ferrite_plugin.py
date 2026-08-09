#!/usr/bin/env python3
"""
Ferrite Plugin for Hermes Agent / OpenClaw
==========================================
Shared plugin — works for both Hermes (default profile) and OpenClaw
(any other Hermes-based profile). Auto-loads on gateway startup.

Registers MCP tools for Ferrite's temporal knowledge graph and
hooks into session lifecycle for auto-ingestion.

INSTALL: Copy to ~/.hermes/plugins/ferrite_plugin.py
CONFIG:  Add to config.yaml:
  ferrite:
    endpoint: http://localhost:8000    # Ferrite MCP server
    api_key_env: FERRITE_API_KEY        # env var name (not the key)
    auto_ingest: true                   # auto-store session transcripts
    namespace: default                  # agent namespace for multi-agent isolation
"""

import json
import os
import requests
from datetime import datetime, timezone


# ── Configuration ──────────────────────────────────────────────

def _get_config():
    """Read Ferrite config from Hermes config.yaml."""
    # TODO: Parse from ~/.hermes/config.yaml → ferrite section
    # For now, use env vars as fallback
    return {
        "endpoint": os.getenv("FERRITE_ENDPOINT", "http://localhost:8000"),
        "api_key": os.getenv("FERRITE_API_KEY", ""),
        "auto_ingest": os.getenv("FERRITE_AUTO_INGEST", "true").lower() == "true",
        "namespace": os.getenv("FERRITE_NAMESPACE", "default"),
    }


def _check_requirements() -> bool:
    """Only activate plugin if Ferrite endpoint is configured."""
    return bool(os.getenv("FERRITE_ENDPOINT") or os.getenv("FERRITE_API_KEY"))


# ── MCP Tool Implementations ──────────────────────────────────

def ferrite_search(query: str, limit: int = 10, namespace: str = None) -> str:
    """
    Semantic search across the temporal knowledge graph.
    Returns matching entities, facts, and episodes with provenance.
    """
    cfg = _get_config()
    try:
        resp = requests.post(
            f"{cfg['endpoint']}/mcp",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {
                        "query": query,
                        "limit": limit,
                        "namespace": namespace or cfg["namespace"],
                    }
                }
            },
            timeout=15,
        )
        return json.dumps(resp.json(), indent=2)
    except requests.RequestException as e:
        return json.dumps({"error": f"Ferrite search failed: {e}"})


def ferrite_store(text: str, source: str = "", metadata: dict = None) -> str:
    """
    Store a fact or observation in the knowledge graph.
    Creates entity/relation triples via extraction pipeline.
    """
    cfg = _get_config()
    try:
        resp = requests.post(
            f"{cfg['endpoint']}/mcp",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={
                "method": "tools/call",
                "params": {
                    "name": "store",
                    "arguments": {
                        "text": text,
                        "source": source,
                        "metadata": metadata or {},
                        "namespace": cfg["namespace"],
                    }
                }
            },
            timeout=30,
        )
        return json.dumps(resp.json(), indent=2)
    except requests.RequestException as e:
        return json.dumps({"error": f"Ferrite store failed: {e}"})


def ferrite_context(topic: str, depth: int = 2) -> str:
    """
    Get relevant context from the knowledge graph for a topic.
    Returns entities, relations, and temporal history within N hops.
    """
    cfg = _get_config()
    try:
        resp = requests.post(
            f"{cfg['endpoint']}/mcp",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={
                "method": "tools/call",
                "params": {
                    "name": "get_context",
                    "arguments": {
                        "topic": topic,
                        "depth": depth,
                        "namespace": cfg["namespace"],
                    }
                }
            },
            timeout=15,
        )
        return json.dumps(resp.json(), indent=2)
    except requests.RequestException as e:
        return json.dumps({"error": f"Ferrite context failed: {e}"})


def ferrite_provenance(entity_id: str) -> str:
    """
    Trace the provenance chain for an entity:
    agent → channel → session → episode → source.
    """
    cfg = _get_config()
    try:
        resp = requests.post(
            f"{cfg['endpoint']}/mcp",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={
                "method": "tools/call",
                "params": {
                    "name": "get_provenance",
                    "arguments": {"entity_id": entity_id}
                }
            },
            timeout=10,
        )
        return json.dumps(resp.json(), indent=2)
    except requests.RequestException as e:
        return json.dumps({"error": f"Ferrite provenance failed: {e}"})


# ── Session Lifecycle Hook ────────────────────────────────────

def on_session_end(session_id: str, transcript_path: str = None) -> str:
    """
    Auto-ingest session transcript to Ferrite on session end.
    Called by Hermes lifecycle hook — no system prompt needed.
    """
    cfg = _get_config()
    if not cfg["auto_ingest"]:
        return json.dumps({"skipped": "auto_ingest disabled"})

    # TODO: Read session JSONL from transcript_path
    # TODO: Parse deterministically (JSONL → structured messages)
    # TODO: POST to Ferrite ingestion endpoint
    # For now, stub:
    return json.dumps({
        "status": "stub",
        "message": "Session ingestion not yet implemented. "
                   "Ferrite server not running.",
        "session_id": session_id,
        "transcript_path": transcript_path,
    })


# ── Plugin Registration ───────────────────────────────────────

# TODO: Register with Hermes tool registry when plugin API is finalized:
#
# from tools.registry import registry
#
# registry.register(
#     name="ferrite_search",
#     toolset="ferrite",
#     schema={
#         "name": "ferrite_search",
#         "description": "Search the Ferrite temporal knowledge graph. "
#                        "Returns entities, facts, and episodes with provenance.",
#         "parameters": {
#             "type": "object",
#             "properties": {
#                 "query": {"type": "string", "description": "Natural language search query"},
#                 "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
#                 "namespace": {"type": "string", "description": "Namespace scope (default: agent's namespace)"},
#             },
#             "required": ["query"],
#         },
#     },
#     handler=lambda args, **kw: ferrite_search(
#         query=args.get("query", ""),
#         limit=args.get("limit", 10),
#         namespace=args.get("namespace"),
#     ),
#     check_fn=_check_requirements,
#     requires_env=["FERRITE_ENDPOINT"],
# )
#
# Similarly for ferrite_store, ferrite_context, ferrite_provenance
#
# TODO: Register lifecycle hook:
# lifecycle.register("on_session_end", on_session_end)


# ── Module init (runs on import) ──────────────────────────────

if _check_requirements():
    # TODO: Auto-register when plugin API is live
    pass
else:
    # Silent — Ferrite not configured, plugin stays dormant
    pass
