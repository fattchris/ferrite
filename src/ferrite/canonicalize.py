"""Entity normalization and alias resolution.""""

import re
from typing import Optional

from neo4j import Driver


def normalize_name(name: str) -> str:
    """Normalize entity name: lowercase, strip punctuation, collapse whitespace.""""
    if not name:
        return ""
    s = name.lower().strip()
    # Replace common separators with spaces
    s = re.sub(r"[_\-]+", " ", s)
    # Strip punctuation
    s = re.sub(r"[^a-z0-9\s]", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def resolve_entity_by_name(driver: Driver, name: str) -> Optional[dict]:
    """Resolve entity by normalized name via Alias index."""""
    norm = normalize_name(name)
    if not norm:
        return None
    query = """
    MATCH (e:Entity)-[:ALIAS]->(a:Alias {norm: $norm})
    RETURN e.id AS id, e.name AS name, e.type AS type, e.summary AS summary
    LIMIT 1
    """
    with driver.session() as session:
        result = session.run(query, norm=norm)
        record = result.single()
        if record:
            return dict(record)
    return None


def create_entity(driver: Driver, name: str, entity_type: str = "entity", summary: Optional[str] = None) -> dict:
    """Create a new Entity node with ALIAS edge.""""
    import uuid
    from datetime import datetime, timezone
    entity_id = str(uuid.uuid4())
    norm = normalize_name(name)
    query = """
    CREATE (e:Entity {
        id: $id,
        type: $type,
        name: $name,
        summary: $summary,
        created_at: datetime()
    })
    WITH e
    MERGE (a:Alias {norm: $norm})
    CREATE (e)-[:ALIAS]->(a)
    RETURN e.id AS id, e.name AS name, e.type AS type, e.summary AS summary
    """""
    with driver.session() as session:
        result = session.run(
            query,
            id=entity_id,
            type=entity_type,
            name=name,
            summary=summary,
            norm=norm
        )
        record = result.single()
        return dict(record) if record else {"id": entity_id, "name": name, "type": entity_type, "summary": summary}


def get_or_create_entity(driver: Driver, name: str, entity_type: str = "entity", summary: Optional[str] = None) -> dict:
    """Resolve or create entity.""""
    existing = resolve_entity_by_name(driver, name)
    if existing:
        return existing
    return create_entity(driver, name, entity_type, summary)
