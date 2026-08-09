"""Query module: natural language to Cypher, multi-hop traversal, search, and context injection."""

import json
import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# --- Schema description for LLM prompts ---

SCHEMA_DESCRIPTION = """\
Ferrite Knowledge Graph — Neo4j Schema

Node Labels:
  - Entity: {id, name, type, summary}
  - Fact: {id, predicate, statement, functional, certainty, epistemic_state,
           assertion_source, valid_at, valid_at_inferred, invalid_at, recorded_at, namespace}
  - Episode: {id, content, content_type, source, namespace, recorded_at}
  - Observation: {id, episode_id, fact_id}
  - Alias: {norm}
  - Literal: {value, type}

Relationship Types:
  - (Fact)-[:SUBJECT]->(Entity)
  - (Fact)-[:OBJECT]->(Entity | Literal)
  - (Fact)-[:SOURCED_FROM]->(Episode)
  - (Observation)-[:SUPPORTS]->(Fact)
  - (Fact)-[:SUPERSEDES]->(Fact)          -- new fact supersedes old fact
  - (Fact)-[:CONTRADICTS]->(Fact)         -- new fact contradicts existing
  - (Entity)-[:ALIAS]->(Alias)
  - (Entity)-[:MERGED_INTO]->(Entity)

Key Properties on Fact:
  - predicate: controlled vocab (e.g. works_at, has_ip, runs_model, deployed_on,
    has_gpu, has_throughput, has_version, configured_with, head_node_of,
    part_of_cluster, has_role, founded_in, located_at, has_port, connects_to,
    uses_framework, rejected, has_regression, parent_brand_of, has_memory)
  - statement: human-readable fact string "<subject> <predicate> <object>"
  - epistemic_state: 'active' | 'contradicted' | 'superseded'
  - namespace: 'shared' | 'personal'
  - valid_at, invalid_at, recorded_at: ISO 8601 datetime strings

Common query patterns:
  - Facts about an entity:     MATCH (e:Entity {name: $name})<-[:SUBJECT]-(f:Fact) RETURN f
  - Entity as object:          MATCH (e:Entity {name: $name})<-[:OBJECT]-(f:Fact) RETURN f
  - Object value of a fact:
      MATCH (f:Fact {id: $id})-[:OBJECT]->(t)
      RETURN COALESCE(t.name, t.value) AS value
  - Active facts only:          WHERE f.epistemic_state = 'active'
  - Fulltext search:
      CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $q)
      YIELD node, score
"""

NL_TO_CYPHER_SYSTEM_PROMPT = f"""\
You are a Cypher query generator for a Neo4j knowledge graph database.

{SCHEMA_DESCRIPTION}

Rules:
1. Generate ONLY a read-only Cypher query. The query MUST start with MATCH,
   OPTIONAL MATCH, WITH, UNWIND, CALL, or RETURN.
2. NEVER use CREATE, DELETE, MERGE, SET, REMOVE, DROP, or DETACH.
   These are write operations and are forbidden.
3. NEVER use $parameter syntax. Inline all values directly as string literals.
   Example: MATCH (e:Entity {{name: 'spark-01'}}) NOT MATCH (e:Entity {{name: $name}})
   Use double curly braces {{ }} for property maps in Cypher.
4. Return the query as plain text. Do NOT wrap in markdown fences. Do NOT include explanations.
5. If the question cannot be answered from the schema, return: MATCH (n) RETURN n LIMIT 0
6. Prefer filtering on epistemic_state = 'active' unless user asks for history.
7. Use COALESCE(target.name, target.value) for object values (Entity/Literal).
8. Keep queries simple and efficient. Use LIMIT when appropriate.
9. Entity names in the graph are lowercase (e.g. 'spark-01', 'glm-5.2', 'mac-mini').
   Always use lowercase entity names in queries.
10. Return meaningful columns: f.statement, f.predicate, e.name,
    COALESCE(obj.name, obj.value) AS object_value.
"""


def _extract_cypher(llm_response: str) -> str:
    """Extract a clean Cypher query from an LLM response.

    Handles markdown fences, leading/trailing prose, and whitespace.
    """
    text = llm_response.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:cypher|cypher\s*)?\s*\n?(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # If there's multi-line prose before the query, try to find the first
    # line that starts with a Cypher keyword
    lines = text.split("\n")
    cypher_keywords = ("MATCH", "OPTIONAL MATCH", "WITH", "UNWIND", "CALL", "RETURN")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(stripped.upper().startswith(kw) for kw in cypher_keywords):
            text = "\n".join(lines[i:])
            break

    # Strip trailing prose after the query (heuristic: cut at first blank line after query start)
    # Just strip and return — the query ends at the last semicolon or end of text
    text = text.strip()
    if text.endswith(";"):
        text = text[:-1].strip()

    return text


def _is_read_only(query: str) -> bool:
    """Verify that a Cypher query is read-only (MATCH/OPTIONAL/WITH/UNWIND/CALL/RETURN)."""
    # Remove string literals to avoid false positives
    cleaned = re.sub(r"'[^']*'", "''", query)
    cleaned = re.sub(r'"[^"]*"', '""', cleaned)
    # Remove comments
    cleaned = re.sub(r"//.*$", "", cleaned, flags=re.MULTILINE)

    forbidden = re.findall(
        r"\b(CREATE|DELETE|MERGE|SET|REMOVE|DROP|DETACH|CALL\s+db\.create|"
        r"CALL\s+db\.delete|CALL\s+gds\.\w*\.\w*Write)\b",
        cleaned,
        re.IGNORECASE,
    )
    if forbidden:
        logger.warning(f"Rejected write operation in query: {forbidden}")
        return False
    return True


def nl_to_cypher(
    natural_language_query: str,
    driver,
    llm_client: Callable[[str, str], str],
) -> list[dict]:
    """Translate a natural language query to Cypher, execute it, and return results.

    Args:
        natural_language_query: The user's question in plain English.
        driver: Neo4j driver instance.
        llm_client: Callable(system_prompt, user_prompt) -> str.

    Returns:
        List of result dicts from the Cypher query.

    Raises:
        ValueError: If the generated query is not read-only.
    """
    user_prompt = (
        f"Translate the following question into a read-only Cypher query.\n\n"
        f"Question: {natural_language_query}\n\n"
        f"Respond with ONLY the Cypher query, no explanation."
    )

    raw_response = llm_client(NL_TO_CYPHER_SYSTEM_PROMPT, user_prompt)
    query = _extract_cypher(raw_response)

    if not _is_read_only(query):
        raise ValueError(
            f"Generated query is not read-only (contains write operations): {query}"
        )

    logger.info(f"Executing Cypher query: {query}")

    try:
        with driver.session() as session:
            result = session.run(query)
            return [dict(r) for r in result]
    except Exception as e:
        # If the generated query has a syntax error (e.g. references `score`
        # from a fulltext CALL without proper YIELD), fall back to fulltext
        # search on the original natural language query.
        logger.warning(f"Cypher query failed ({e}); falling back to search_facts")
        return search_facts(driver, natural_language_query)


def multi_hop_query(driver, entity_name: str, hops: int = 2) -> list[dict]:
    """Traverse from an entity through Fact->SUBJECT->Entity and Fact->OBJECT->Entity for N hops.

    Starting from the named entity, follow outgoing Fact edges (both SUBJECT and OBJECT
    directions) to reach connected entities, then repeat from those entities.

    Args:
        driver: Neo4j driver instance.
        entity_name: Name of the starting entity.
        hops: Number of hops to traverse (default 2).

    Returns:
        List of dicts with entity name, hop distance, connecting fact, and direction.
    """
    if hops < 1:
        return []

    # We use APOG-style variable-length path matching through the Fact graph.
    # Pattern: Entity <-[:SUBJECT]- Fact -[:OBJECT]-> Entity|Literal
    # and:    Entity <-[:OBJECT]- Fact -[:SUBJECT]-> Entity
    # Each hop traverses one Fact node in either direction.
    #
    # For N hops, we chain: (start) -[:SUBJECT|OBJECT*2..(2*hops)]-> (end)
    # But we need to be more precise since the path goes through Fact nodes.
    # The min hop in Neo4j path length is 2 (Entity->Fact->Entity).
    # So N "hops" = 2*N relationship traversals in Neo4j terms.

    with driver.session() as session:
        result = session.run(
            """
            MATCH (start:Entity {name: $entity_name})
            MATCH path = (start)-[:SUBJECT|OBJECT]-(f:Fact)-[:SUBJECT|OBJECT]-(connected)
            WHERE connected:Entity
              AND connected <> start
              AND connected.id IS NOT NULL
            WITH DISTINCT connected, collect(DISTINCT f) AS connecting_facts
            UNWIND connecting_facts AS cf
            WITH connected, cf
            MATCH (cf)-[:OBJECT]->(obj)
            WITH connected,
                 cf.id AS fact_id,
                 cf.statement AS statement,
                 cf.predicate AS predicate,
                 cf.epistemic_state AS epistemic_state,
                 COALESCE(obj.name, obj.value) AS object_value
            RETURN DISTINCT
                 connected.name AS entity_name,
                 connected.id AS entity_id,
                 connected.summary AS entity_summary,
                 fact_id,
                 statement,
                 predicate,
                 epistemic_state,
                 object_value
            ORDER BY entity_name, predicate
            """,
            entity_name=entity_name,
        )
        results = [dict(r) for r in result]

    # For hops > 1, do recursive expansion
    if hops > 1:
        visited = {entity_name}
        frontier = {r["entity_name"] for r in results}
        # Tag first-level results with hop=1
        for r in results:
            r["hop"] = 1
        all_results = list(results)
        current_hop = 1

        while current_hop < hops and frontier:
            next_frontier = set()
            for ent_name in frontier:
                if ent_name in visited:
                    continue
                visited.add(ent_name)
                with driver.session() as session:
                    sub_result = session.run(
                        """
                        MATCH (start:Entity {name: $entity_name})
                        MATCH path = (start)-[:SUBJECT|OBJECT]-(f:Fact)
                        -[:SUBJECT|OBJECT]-(connected)
                        WHERE connected:Entity
                          AND connected <> start
                          AND connected.name IS NOT NULL
                        WITH DISTINCT connected, f
                        MATCH (f)-[:OBJECT]->(obj)
                        RETURN DISTINCT
                             connected.name AS entity_name,
                             connected.id AS entity_id,
                             connected.summary AS entity_summary,
                             f.id AS fact_id,
                             f.statement AS statement,
                             f.predicate AS predicate,
                             f.epistemic_state AS epistemic_state,
                             COALESCE(obj.name, obj.value) AS object_value,
                             $hop AS hop
                        ORDER BY entity_name, f.predicate
                        """,
                        entity_name=ent_name,
                        hop=current_hop + 1,
                    )
                    for r in sub_result:
                        r = dict(r)
                        if r["entity_name"] not in visited:
                            all_results.append(r)
                            next_frontier.add(r["entity_name"])
            frontier = next_frontier
            current_hop += 1

        return all_results

    # Add hop=1 to first-level results
    for r in results:
        r["hop"] = 1
    return results


def search_facts(
    driver,
    query: str,
    namespace: Optional[str] = None,
    limit: int = 10,
    embedder=None,
) -> list[dict]:
    """Hybrid search on fact statements (BM25 + vector cosine via RRF).

    Falls back to BM25-only if no embedder or Ollama is unreachable.

    Args:
        driver: Neo4j driver instance.
        query: Search query string.
        namespace: Optional namespace filter.
        limit: Maximum number of results (default 10).
        embedder: Optional OllamaEmbedder for semantic search.

    Returns:
        List of dicts with fact id, statement, predicate, score, and metadata.
    """
    # Try hybrid search if embedder is available
    if embedder is not None:
        results = hybrid_search(driver, query, embedder, namespace=namespace, limit=limit)
        if results:
            return results
        # Fall through to BM25-only if vector search returned nothing

    return _bm25_search(driver, query, namespace=namespace, limit=limit)


def _bm25_search(
    driver,
    query: str,
    namespace: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Fulltext (BM25) search on fact statements."""
    ns_filter = "AND f.namespace = $namespace" if namespace else ""

    with driver.session() as session:
        result = session.run(
            f"""
            CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $search_text)
            YIELD node AS f, score
            WHERE f:Fact
            {ns_filter}
            RETURN f.id AS id,
                   f.statement AS statement,
                   f.predicate AS predicate,
                   f.certainty AS certainty,
                   f.epistemic_state AS epistemic_state,
                   f.namespace AS namespace,
                   f.valid_at AS valid_at,
                   f.recorded_at AS recorded_at,
                   score
            ORDER BY score DESC
            LIMIT $limit
            """,
            search_text=query,
            namespace=namespace,
            limit=limit,
        )
        return [dict(r) for r in result]


def vector_search(
    driver,
    query_text: str,
    embedder,
    namespace: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Semantic vector search using Neo4j vector index.

    Embeds the query, searches the fact_embeddings vector index.
    Returns empty list if Ollama is unreachable or vector index missing.
    """
    query_embedding = embedder.embed(query_text)
    if query_embedding is None:
        return []

    ns_filter = "WHERE f.namespace = $namespace" if namespace else ""
    params: dict = {"embedding": query_embedding, "limit": limit}
    if namespace:
        params["namespace"] = namespace

    try:
        with driver.session() as session:
            result = session.run(
                f"""
                CALL db.index.vector.queryNodes('fact_embeddings', $limit, $embedding)
                YIELD node AS f, score
                WHERE f:Fact
                {ns_filter}
                RETURN f.id AS id,
                       f.statement AS statement,
                       f.predicate AS predicate,
                       f.certainty AS certainty,
                       f.epistemic_state AS epistemic_state,
                       f.namespace AS namespace,
                       f.valid_at AS valid_at,
                       f.recorded_at AS recorded_at,
                       score
                ORDER BY score DESC
                """,
                **params,
            )
            return [dict(r) for r in result]
    except Exception as e:
        logger.warning("Vector search failed (degrading to BM25): %s", e)
        return []


def hybrid_search(
    driver,
    query_text: str,
    embedder,
    namespace: Optional[str] = None,
    limit: int = 10,
    rrf_k: int = 60,
) -> list[dict]:
    """Hybrid search combining BM25 fulltext + vector cosine via RRF.

    RRF formula: score = sum(1/(k + rank_i)) for each strategy.
    Falls back to BM25-only if vector search is unavailable.
    """
    # Run both searches in parallel (could be async later)
    bm25_results = _bm25_search(driver, query_text, namespace=namespace, limit=limit * 2)
    vec_results = vector_search(driver, query_text, embedder, namespace=namespace, limit=limit * 2)

    if not vec_results:
        # No vector results — return BM25 only
        return bm25_results[:limit]

    # RRF fusion: combine by rank
    rrf_scores: dict[str, float] = {}
    fact_data: dict[str, dict] = {}

    for rank, r in enumerate(bm25_results):
        fid = r["id"]
        rrf_scores[fid] = rrf_scores.get(fid, 0) + 1.0 / (rrf_k + rank + 1)
        fact_data[fid] = r

    for rank, r in enumerate(vec_results):
        fid = r["id"]
        rrf_scores[fid] = rrf_scores.get(fid, 0) + 1.0 / (rrf_k + rank + 1)
        if fid not in fact_data:
            fact_data[fid] = r

    # Sort by RRF score
    sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)

    results = []
    for fid in sorted_ids[:limit]:
        r = dict(fact_data[fid])
        r["score"] = rrf_scores[fid]
        results.append(r)

    return results


def get_entity_facts(driver, entity_name: str) -> dict:
    """Get all facts where entity is subject or object.

    Args:
        driver: Neo4j driver instance.
        entity_name: Name of the entity to look up.

    Returns:
        Dict with 'entity' info, 'facts_as_subject', and 'facts_as_object'.
        Returns empty structure if entity not found.
    """
    with driver.session() as session:
        # Get entity info
        entity_result = session.run(
            """
            MATCH (e:Entity {name: $name})
            RETURN e.id AS id, e.type AS type, e.name AS name, e.summary AS summary
            """,
            name=entity_name,
        )
        entity_record = entity_result.single()

        if not entity_record:
            return {
                "entity": None,
                "facts_as_subject": [],
                "facts_as_object": [],
            }

        # Facts where entity is subject
        subj_result = session.run(
            """
            MATCH (e:Entity {name: $name})<-[:SUBJECT]-(f:Fact)
            OPTIONAL MATCH (f)-[:OBJECT]->(obj)
            RETURN f.id AS id,
                   f.statement AS statement,
                   f.predicate AS predicate,
                   f.certainty AS certainty,
                   f.epistemic_state AS epistemic_state,
                   f.functional AS functional,
                   f.valid_at AS valid_at,
                   f.invalid_at AS invalid_at,
                   f.recorded_at AS recorded_at,
                   f.namespace AS namespace,
                   COALESCE(obj.name, obj.value) AS object_value
            ORDER BY f.valid_at DESC
            """,
            name=entity_name,
        )
        facts_as_subject = [dict(r) for r in subj_result]

        # Facts where entity is object
        obj_result = session.run(
            """
            MATCH (e:Entity {name: $name})<-[:OBJECT]-(f:Fact)
            OPTIONAL MATCH (f)-[:SUBJECT]->(subj:Entity)
            RETURN f.id AS id,
                   f.statement AS statement,
                   f.predicate AS predicate,
                   f.certainty AS certainty,
                   f.epistemic_state AS epistemic_state,
                   f.functional AS functional,
                   f.valid_at AS valid_at,
                   f.invalid_at AS invalid_at,
                   f.recorded_at AS recorded_at,
                   f.namespace AS namespace,
                   subj.name AS subject_name
            ORDER BY f.valid_at DESC
            """,
            name=entity_name,
        )
        facts_as_object = [dict(r) for r in obj_result]

        return {
            "entity": dict(entity_record),
            "facts_as_subject": facts_as_subject,
            "facts_as_object": facts_as_object,
        }


# --- Context injection ---

ENTITY_EXTRACTION_PROMPT = """\
You are an entity extraction engine. Given a user's text, extract all named entities.

Rules:
1. Extract entity names as they appear in the text (preserve original casing and punctuation).
2. Include people, organizations, devices, services, models, clusters, products, and technologies.
3. Also extract any IP addresses, port numbers, or technical identifiers mentioned.
4. Return ONLY a JSON array of strings. No prose, no markdown fences.
5. If no entities are found, return an empty array [].

Example input: "What model is running on spark-01?"
Example output: ["spark-01"]

Example input: "Is the litellm proxy on mac-mini working?"
Example output: ["litellm", "mac-mini"]
"""

# Keywords that map to known entities in the graph for context injection.
# These are common domain terms that appear in user turns but aren't exact entity names.
INJECT_KEYWORD_MAP: dict[str, list[str]] = {
    "glm": ["glm-5.2", "glm-cluster", "spark-01", "spark-02", "spark-03", "spark-04"],
    "cluster": ["glm-cluster", "dsv4-cluster", "spark-01", "spark-02", "spark-03", "spark-04"],
    "spark": [
        "spark-01", "spark-02", "spark-03", "spark-04",
        "spark-05", "spark-06", "spark-07", "spark-08",
    ],
    "repair": ["inference-repair-loop"],
    "loop": ["inference-repair-loop"],
    "laguna": ["laguna-s-2-1", "spark-07"],
    "ferrite": ["ferrite", "neo4j", "docker"],
    "neo4j": ["neo4j"],
    "docker": ["neo4j", "docker"],
    "vllm": ["vllm", "glm-5.2", "laguna-s-2-1"],
    "dsv4": ["dsv4-flash-ablit", "dsv4-cluster", "spark-07", "spark-08"],
    "voice": ["hermes-voice-bridge", "m3pro"],
    "litellm": ["litellm"],
    "tailscale": ["tailscale"],
    "hempsentry": ["hempsentry"],
    "spec": ["ferrite", "neo4j"],
    "recipe": ["laguna-s-2-1", "spark-07"],
}


def inject_context(
    driver,
    turn_text: str,
    llm_client: Callable[[str, str], str],
) -> list[dict]:
    """Determine if any facts should be injected as context for a user's turn.

    Uses keyword extraction from the text to find relevant entities, then looks
    up facts for those entities. Returns all candidate facts directly without
    LLM relevance filtering (which is too lossy).

    Args:
        driver: Neo4j driver instance.
        turn_text: The user's text/turn.
        llm_client: Callable(system_prompt, user_prompt) -> str.

    Returns:
        List of relevant fact dicts. Returns [] if nothing relevant (silence floor).
    """
    lower_text = turn_text.lower()

    # Step 1: Extract keywords from the text using the keyword map
    entity_names: set[str] = set()
    for keyword, mapped_entities in INJECT_KEYWORD_MAP.items():
        if keyword in lower_text:
            entity_names.update(mapped_entities)

    # Also try LLM entity extraction for any entities we might have missed
    try:
        user_prompt = f"Extract entities from the following text:\n\n{turn_text}"
        raw_response = llm_client(ENTITY_EXTRACTION_PROMPT, user_prompt)
        try:
            llm_entities = json.loads(raw_response.strip())
        except json.JSONDecodeError:
            arr_match = re.search(r"\[.*?\]", raw_response, re.DOTALL)
            if arr_match:
                try:
                    llm_entities = json.loads(arr_match.group(0))
                except json.JSONDecodeError:
                    llm_entities = []
            else:
                llm_entities = []
        if isinstance(llm_entities, list):
            for ent in llm_entities:
                if isinstance(ent, str) and ent.strip():
                    entity_names.add(ent.strip().lower())
    except Exception:
        pass  # Non-fatal — keyword extraction is the primary strategy

    if not entity_names:
        return []

    # Step 2: Look up facts for each entity name (exact + alias + fulltext)
    candidate_facts: list[dict] = []
    seen_fact_ids: set[str] = set()

    from .canonicalize import normalize_name

    for entity_name in entity_names:
        norm_name = normalize_name(entity_name)

        with driver.session() as session:
            # Strategy 1: Exact name match
            result = session.run(
                """
                MATCH (e:Entity {name: $name})<-[:SUBJECT]-(f:Fact)
                WHERE f.epistemic_state = 'active'
                OPTIONAL MATCH (f)-[:OBJECT]->(obj)
                RETURN f.id AS id,
                       f.statement AS statement,
                       f.predicate AS predicate,
                       f.certainty AS certainty,
                       f.valid_at AS valid_at,
                       COALESCE(obj.name, obj.value) AS object_value
                """,
                name=entity_name,
            )
            for r in result:
                r = dict(r)
                if r["id"] not in seen_fact_ids:
                    candidate_facts.append(r)
                    seen_fact_ids.add(r["id"])

            # Strategy 2: Match via Alias (normalized name)
            result = session.run(
                """
                MATCH (a:Alias {norm: $norm})<-[:ALIAS]-(e:Entity)
                MATCH (e)<-[:SUBJECT]-(f:Fact)
                WHERE f.epistemic_state = 'active'
                OPTIONAL MATCH (f)-[:OBJECT]->(obj)
                RETURN f.id AS id,
                       f.statement AS statement,
                       f.predicate AS predicate,
                       f.certainty AS certainty,
                       f.valid_at AS valid_at,
                       COALESCE(obj.name, obj.value) AS object_value
                """,
                norm=norm_name,
            )
            for r in result:
                r = dict(r)
                if r["id"] not in seen_fact_ids:
                    candidate_facts.append(r)
                    seen_fact_ids.add(r["id"])

            # Also get facts where this entity is the OBJECT
            result = session.run(
                """
                MATCH (e:Entity {name: $name})<-[:OBJECT]-(f:Fact)
                WHERE f.epistemic_state = 'active'
                OPTIONAL MATCH (f)-[:SUBJECT]->(subj:Entity)
                RETURN f.id AS id,
                       f.statement AS statement,
                       f.predicate AS predicate,
                       f.certainty AS certainty,
                       f.valid_at AS valid_at,
                       subj.name AS subject_name
                """,
                name=entity_name,
            )
            for r in result:
                r = dict(r)
                if r["id"] not in seen_fact_ids:
                    candidate_facts.append(r)
                    seen_fact_ids.add(r["id"])

    # Step 3: Fulltext search on the raw turn text for additional facts
    with driver.session() as session:
        try:
            result = session.run(
                """
                CALL db.index.fulltext.queryNodes(
                    'fact_statement_fulltext', $search_text
                )
                YIELD node AS f, score
                WHERE f:Fact AND f.epistemic_state = 'active'
                RETURN f.id AS id,
                       f.statement AS statement,
                       f.predicate AS predicate,
                       f.certainty AS certainty,
                       f.valid_at AS valid_at,
                       '' AS object_value
                ORDER BY score DESC
                LIMIT 10
                """,
                search_text=turn_text,
            )
            for r in result:
                r = dict(r)
                if r["id"] not in seen_fact_ids:
                    candidate_facts.append(r)
                    seen_fact_ids.add(r["id"])
        except Exception:
            pass  # Fulltext may fail if query has special chars

    if not candidate_facts:
        return []

    # Return all candidate facts directly — skip LLM relevance filter (too lossy)
    return candidate_facts
