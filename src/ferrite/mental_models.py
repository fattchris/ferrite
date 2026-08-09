"""Mental models and disposition traits.

Per spec §3.6 (A16) and §3.7:

Mental Models (§3.6):
- User-curated summaries for common query patterns
- Stored as mental_model nodes with CURATED_FOR edges to entities/concepts
- Checked first in recall priority: Mental Models → Observations → Raw Facts
- Updates are manual, prompted by staleness flag
- LLM-drafted and user-approved (or manually authored)

Disposition Traits (§3.7):
- Per-namespace configuration shaping consolidation LLM behavior
- skepticism (1-5), literalism (1-5), empathy (1-5)
- Applied ONLY in consolidation pass, not search/retrieval/ranking
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# --- Disposition defaults per namespace (§3.7) ---

DEFAULT_DISPOSITIONS: dict[str, dict[str, int]] = {
    "shared": {"skepticism": 3, "literalism": 3, "empathy": 3},
    "personal": {"skepticism": 2, "literalism": 4, "empathy": 4},
}


def get_disposition(namespace: str = "shared",
                    overrides: Optional[dict] = None) -> dict[str, int]:
    """Get disposition traits for a namespace.

    Merges defaults with optional overrides from config.
    Returns dict with skepticism, literalism, empathy (each 1-5).
    """
    base = DEFAULT_DISPOSITIONS.get(namespace, DEFAULT_DISPOSITIONS["shared"])
    if overrides and namespace in overrides:
        base = {**base, **overrides[namespace]}
    return base


# --- Mental Model CRUD ---

def create_mental_model(
    driver,
    title: str,
    summary: str,
    curated_for: list[str],  # entity names
    namespace: str = "shared",
    tags: Optional[list[str]] = None,
    llm_drafted: bool = False,
    approved_by: Optional[str] = None,
) -> str:
    """Create a mental model node with CURATED_FOR edges to entities.

    Args:
        driver: Neo4j driver.
        title: Human-readable title (e.g. "Spark fleet deployment patterns").
        summary: The curated summary text.
        curated_for: List of entity names this model summarizes.
        namespace: Namespace scope.
        tags: Optional topic tags for search.
        llm_drafted: True if LLM-generated (requires user approval).
        approved_by: User who approved this model (None = manually authored).

    Returns:
        The mental model node ID.
    """
    model_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with driver.session() as s:
        # Create the MentalModel node
        s.run(
            """
            CREATE (m:mental_model {
                id: $id,
                title: $title,
                summary: $summary,
                namespace: $namespace,
                tags: $tags,
                llm_drafted: $llm_drafted,
                approved_by: $approved_by,
                stale: false,
                created_at: datetime($now),
                updated_at: datetime($now)
            })
            """,
            id=model_id,
            title=title,
            summary=summary,
            namespace=namespace,
            tags=tags or [],
            llm_drafted=llm_drafted,
            approved_by=approved_by,
            now=now,
        ).consume()

        # Create CURATED_FOR edges to entities
        for entity_name in curated_for:
            s.run(
                """
                MATCH (m:mental_model {id: $model_id}), (e:Entity {name: $entity_name})
                CREATE (m)-[:CURATED_FOR]->(e)
                """,
                model_id=model_id,
                entity_name=entity_name,
            ).consume()

    logger.info("Created mental model %s: '%s' for %d entities",
                model_id, title, len(curated_for))
    return model_id


def get_mental_model(driver, model_id: str) -> Optional[dict]:
    """Get a mental model by ID with its curated entities."""
    with driver.session() as s:
        result = s.run(
            """
            MATCH (m:mental_model {id: $id})
            OPTIONAL MATCH (m)-[:CURATED_FOR]->(e:Entity)
            RETURN m.id AS id,
                   m.title AS title,
                   m.summary AS summary,
                   m.namespace AS namespace,
                   m.tags AS tags,
                   m.llm_drafted AS llm_drafted,
                   m.approved_by AS approved_by,
                   m.stale AS stale,
                   m.created_at AS created_at,
                   m.updated_at AS updated_at,
                   collect(DISTINCT e.name) AS curated_entities
            """,
            id=model_id,
        )
        record = result.single()
        return dict(record) if record else None


def search_mental_models(
    driver,
    query: str,
    namespace: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """Search mental models by title/summary/tags.

    Mental models are checked first in recall priority (§3.6).
    """
    ns_filter = "AND m.namespace = $namespace" if namespace else ""

    params: dict = {"query": query, "limit": limit}
    if namespace:
        params["namespace"] = namespace

    with driver.session() as s:
        try:
            result = s.run(
                f"""
                CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $query)
                YIELD node, score
                WHERE node:mental_model
                {ns_filter}
                RETURN node.id AS id,
                       node.title AS title,
                       node.summary AS summary,
                       node.namespace AS namespace,
                       node.tags AS tags,
                       node.stale AS stale,
                       score
                ORDER BY score DESC
                LIMIT $limit
                """,
                **params,
            )
            return [dict(r) for r in result]
        except Exception:
            # Fallback: no fulltext index on mental_model yet
            result = s.run(
                f"""
                MATCH (m:mental_model)
                WHERE toLower(m.title) CONTAINS toLower($query)
                   OR toLower(m.summary) CONTAINS toLower($query)
                {ns_filter}
                RETURN m.id AS id,
                       m.title AS title,
                       m.summary AS summary,
                       m.namespace AS namespace,
                       m.tags AS tags,
                       m.stale AS stale
                LIMIT $limit
                """,
                **params,
            )
            return [dict(r) for r in result]


def update_mental_model(
    driver,
    model_id: str,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    tags: Optional[list[str]] = None,
    stale: Optional[bool] = None,
) -> None:
    """Update a mental model. Manual updates only (§3.6, A16)."""
    updates = []
    params: dict = {"id": model_id, "now": datetime.now(timezone.utc).isoformat()}

    if title is not None:
        updates.append("m.title = $title")
        params["title"] = title
    if summary is not None:
        updates.append("m.summary = $summary")
        params["summary"] = summary
    if tags is not None:
        updates.append("m.tags = $tags")
        params["tags"] = tags
    if stale is not None:
        updates.append("m.stale = $stale")
        params["stale"] = stale

    if not updates:
        return

    updates.append("m.updated_at = datetime($now)")

    with driver.session() as s:
        s.run(
            f"MATCH (m:mental_model {{id: $id}}) SET {', '.join(updates)}",
            **params,
        ).consume()

    logger.info("Updated mental model %s", model_id)


def flag_stale_mental_models(driver) -> int:
    """Flag mental models whose underlying facts have changed.

    Called by the consolidation consumer when it processes a group —
    if the group's entity has a mental model, flag it stale.

    Returns count of models flagged stale.
    """
    datetime.now(timezone.utc).isoformat()

    with driver.session() as s:
        # Find mental models whose curated entities have facts
        # updated after the model's last update
        result = s.run(
            """
            MATCH (m:mental_model)-[:CURATED_FOR]->(e:Entity)
            MATCH (e)<-[:SUBJECT]-(f:Fact)
            WHERE m.stale = false
              AND f.recorded_at > m.updated_at
            WITH DISTINCT m
            SET m.stale = true
            RETURN count(m) AS count
            """,
        )
        record = result.single()
        count = record["count"] if record else 0

    if count > 0:
        logger.info("Flagged %d mental models as stale", count)
    return count


def draft_mental_model(
    driver,
    entity_name: str,
    llm_client,
    namespace: str = "shared",
    title: Optional[str] = None,
) -> Optional[str]:
    """LLM-draft a mental model for an entity (requires user approval, §3.6).

    Generates a summary of all active facts about an entity, creates a
    mental_model node with llm_drafted=true. User must approve via update.
    """
    # Gather all active facts about this entity
    with driver.session() as s:
        result = s.run(
            """
            MATCH (e:Entity {name: $name})<-[:SUBJECT]-(f:Fact)
            WHERE f.epistemic_state = 'active'
            OPTIONAL MATCH (f)-[:OBJECT]->(obj)
            RETURN f.statement AS statement,
                   f.predicate AS predicate,
                   f.certainty AS certainty,
                   f.assertion_source AS assertion_source,
                   COALESCE(obj.name, obj.value) AS object_value,
                   f.recorded_at AS recorded_at
            ORDER BY f.recorded_at DESC
            """,
            name=entity_name,
        )
        facts = [dict(r) for r in result]

    if not facts:
        logger.info("No facts for %s — skipping mental model draft", entity_name)
        return None

    # LLM summary
    fact_lines = [f"- {f['statement']}" for f in facts]
    system_prompt = """\
You are a knowledge synthesis engine. Create a readable brief summarizing
what we know about an entity based on the provided facts.

Rules:
1. Write 2-5 paragraphs.
2. Group related facts together.
3. Note temporal changes (when things changed).
4. Flag any contradictions.
5. Return only the summary text.
"""
    user_prompt = f"Entity: {entity_name}\n\nFacts:\n{''.join(fact_lines)}"
    summary = llm_client(system_prompt, user_prompt)

    model_title = title or f"{entity_name} — overview"

    model_id = create_mental_model(
        driver,
        title=model_title,
        summary=summary,
        curated_for=[entity_name],
        namespace=namespace,
        llm_drafted=True,
        approved_by=None,
    )

    logger.info("LLM-drafted mental model %s for %s (needs approval)",
                model_id, entity_name)
    return model_id


def get_recall_priority(
    driver,
    query: str,
    embedder=None,
    namespace: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Get results in recall priority order (§3.8):

    1. Mental Models (if match exists for query topic)
    2. Observations (consolidated, high proof_count)
    3. Raw Facts (ground truth, always available)
    """
    results: list[dict] = []

    # 1. Mental models
    models = search_mental_models(driver, query, namespace=namespace, limit=3)
    for m in models:
        m["result_type"] = "mental_model"
        results.append(m)

    # 2. Observations
    with driver.session() as s:
        obs_result = s.run(
            """
            CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $query)
            YIELD node, score
            WHERE node:Observation
              AND node.epistemic_state = 'active'
            """ + ("AND node.namespace = $namespace" if namespace else "") + """
            RETURN node.id AS id,
                   node.summary AS summary,
                   node.predicate AS predicate,
                   node.namespace AS namespace,
                   node.proof_count AS proof_count,
                   node.epistemic_state AS epistemic_state,
                   score
            ORDER BY score DESC, node.proof_count DESC
            LIMIT $limit
            """,
            query=query,
            limit=limit,
            **({"namespace": namespace} if namespace else {}),
        )
        for r in obs_result:
            r = dict(r)
            r["result_type"] = "observation"
            results.append(r)

    # 3. Raw facts via TEMPR
    from .tempr import tempr_search

    facts = tempr_search(
        driver, query, embedder=embedder,
        namespace=namespace, limit=limit,
    )
    for f in facts:
        f["result_type"] = "fact"
        results.append(f)

    return results[:limit]
