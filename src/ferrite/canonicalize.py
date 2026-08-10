"""Entity canonicalization: normalization, alias resolution, and merge logic."""

import logging
import re
from typing import Callable, Optional

from neo4j import Driver

from .embeddings import cosine_similarity
from .models import Entity

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize an entity name for alias matching.

    Steps:
    1. Lowercase
    2. Strip leading/trailing whitespace
    3. Normalize separators (hyphens, slashes, underscores to spaces)
    4. Strip punctuation (keep alphanumerics and spaces)
    5. Collapse whitespace
    """
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"[-_/]", " ", s)
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def get_embedding(embedding_func: Callable[[str], list[float]], text: str) -> list[float]:
    """Call the embedding function and return a vector."""
    return embedding_func(text)


def resolve_entity(
    driver: Driver,
    name: str,
    embedding_func: Optional[Callable] = None,
    entity_type: str = "entity",
    summary: Optional[str] = None,
) -> Entity:
    """Resolve an entity by name through alias lookup and embedding match.

    Resolution steps:
    1. Normalize name.
    2. Check exact alias lookup in Neo4j.
    3. If no alias hit, compute embedding and do ANN search.
    4. If similarity >= 0.95, auto-merge (create MERGED_INTO edge).
    5. If 0.80 <= similarity < 0.95, LLM adjudication (Phase 1 stub: create new).
    6. If < 0.80, create new Entity + ALIAS edge.

    Returns the resolved Entity object.
    """
    norm = normalize_name(name)
    if not norm:
        raise ValueError("Cannot resolve empty entity name")

    # Step 2: Exact alias lookup
    with driver.session() as session:
        result = session.run(
            """
            MATCH (e:Entity)-[:ALIAS]->(a:Alias {norm: $norm})
            RETURN e.id AS id, e.type AS type, e.name AS name, e.summary AS summary
            LIMIT 1
            """,
            norm=norm,
        )
        record = result.single()

    if record:
        logger.info(f"Alias hit for '{name}' -> entity {record['id']}")
        return Entity(
            id=record["id"],
            type=record["type"],
            name=record["name"],
            summary=record["summary"],
        )

    # Step 3: Embedding-based ANN search via Neo4j vector index
    best_entity = None
    best_similarity = 0.0

    if embedding_func is not None:
        query_embedding = get_embedding(embedding_func, name)

        with driver.session() as session:
            try:
                # Use Neo4j vector index for ANN search (§7.2.2)
                result = session.run(
                    """
                    CALL db.index.vector.queryNodes('entity_embeddings', 5, $embedding)
                    YIELD node AS e, score
                    RETURN e.id AS id, e.type AS type, e.name AS name,
                           e.summary AS summary, score
                    """,
                    embedding=query_embedding,
                )
                for record in result:
                    sim = record["score"]
                    if sim > best_similarity:
                        best_similarity = sim
                        best_entity = record
            except Exception as e:
                # Fallback: scan all entities with embeddings (pre-index path)
                logger.debug(
                    "Vector index query failed, falling back to full scan: %s", e
                )
                result = session.run(
                    """
                    MATCH (e:Entity)
                    WHERE e.embedding IS NOT NULL
                    RETURN e.id AS id, e.type AS type, e.name AS name,
                           e.summary AS summary, e.embedding AS embedding
                    """
                )
                for record in result:
                    sim = cosine_similarity(query_embedding, record["embedding"])
                    if sim > best_similarity:
                        best_similarity = sim
                        best_entity = record

    # Step 4: Auto-merge at >= 0.95
    if best_entity and best_similarity >= 0.95:
        logger.info(
            f"Auto-merge '{name}' (sim={best_similarity:.3f}) -> entity {best_entity['id']}"
        )
        _create_alias(driver, best_entity["id"], norm)
        return Entity(
            id=best_entity["id"],
            type=best_entity["type"],
            name=best_entity["name"],
            summary=best_entity["summary"],
        )

    # Step 5: 0.80-0.95 LLM adjudication (Phase 1 stub: create new)
    if best_entity and 0.80 <= best_similarity < 0.95:
        logger.info(
            f"Ambiguous match '{name}' (sim={best_similarity:.3f}); "
            "LLM adjudication not yet implemented, creating new entity."
        )

    # Step 6: Create new entity
    return create_entity(driver, name, entity_type, summary, embedding_func)


def create_entity(
    driver: Driver,
    name: str,
    entity_type: str = "entity",
    summary: Optional[str] = None,
    embedding_func: Optional[Callable] = None,
) -> Entity:
    """Create a new Entity node plus an ALIAS edge for the normalized name."""
    entity = Entity(type=entity_type, name=name, summary=summary)
    norm = normalize_name(name)

    embedding_val = None
    if embedding_func is not None:
        embedding_val = get_embedding(embedding_func, name)

    with driver.session() as session:
        session.run(
            """
            CREATE (e:Entity {
                id: $id, type: $type, name: $name, summary: $summary
            })
            WITH e
            MERGE (a:Alias {norm: $norm})
            CREATE (e)-[:ALIAS]->(a)
            """,
            id=entity.id,
            type=entity.type,
            name=entity.name,
            summary=entity.summary,
            norm=norm,
        )

        if embedding_val is not None:
            session.run(
                """
                MATCH (e:Entity {id: $id})
                SET e.embedding = $embedding
                """,
                id=entity.id,
                embedding=embedding_val,
            )

    logger.info(f"Created new entity '{name}' (id={entity.id})")
    return entity


def _create_alias(driver: Driver, entity_id: str, norm: str) -> None:
    """Create an ALIAS edge from an existing entity to a normalized name."""
    with driver.session() as session:
        session.run(
            """
            MATCH (e:Entity {id: $entity_id})
            MERGE (a:Alias {norm: $norm})
            MERGE (e)-[:ALIAS]->(a)
            """,
            entity_id=entity_id,
            norm=norm,
        )


def merge_entities(driver: Driver, source_id: str, target_id: str) -> None:
    """Create a MERGED_INTO edge from source entity to target entity.
    Merges are additive — never destructive.
    """
    with driver.session() as session:
        session.run(
            """
            MATCH (source:Entity {id: $source_id}), (target:Entity {id: $target_id})
            CREATE (source)-[:MERGED_INTO]->(target)
            """,
            source_id=source_id,
            target_id=target_id,
        )
    logger.info(f"Merged entity {source_id} into {target_id}")
