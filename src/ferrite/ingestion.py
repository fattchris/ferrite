"""Ingestion pipeline: queue episodes, extract, canonicalize, and write to Neo4j.""""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from neo4j import Driver

from ferrite.canonicalize import get_or_create_entity
from ferrite.config import get_settings
from ferrite.extractor import extract, generate_statement
from ferrite.models import Episode, Namespace
from ferrite.temporal import apply_supersession
from ferrite.vocab import is_functional

logger = logging.getLogger(__name__)

QUEUE_KEY = "ferrite:ingestion:queue"


async def queue_episode(
    redis: aioredis.Redis,
    content: str,
    content_type: str,
    source: dict,
    namespace: str = None,
) -> dict:
    """Queue an episode for ingestion. Returns episode_id and status.""""
    settings = get_settings()
    ns = namespace or settings.NAMESPACE_DEFAULT
    episode = Episode(
        content=content,
        content_type=content_type,
        source=source,
        namespace=Namespace(ns)
    )
    await redis.lpush(QUEUE_KEY, episode.model_dump_json())
    return {"episode_id": episode.id, "status": "queued"}


async def process_episode(redis: aioredis.Redis, driver: Driver) -> Optional[dict]:
    """Pop and process a single episode from the queue."""""
    raw = await redis.rpop(QUEUE_KEY)
    if not raw:
        return None

    episode = Episode.model_validate_json(raw)
    try:
        result = await extract(episode.content, episode.content_type)
        return await _write_to_graph(driver, episode, result)
    except Exception as e:
        logger.error(f"Failed to process episode {episode.id}: {e}")
        return {"episode_id": episode.id, "status": "failed", "error": str(e)}


async def _write_to_graph(driver: Driver, episode: Episode, result) -> dict:
    """Write extracted entities and facts to Neo4j.""""
    # 1. Create/resolve entities
    entity_map: dict[str, dict] = {}
    for ent in result.entities:
        e = get_or_create_entity(driver, ent.name, ent.type, ent.summary)
        entity_map[ent.name] = e

    # 2. Create episode node
    _create_episode_node(driver, episode)

    facts_written = 0
    for ef in result.facts:
        # Resolve subject entity
        if ef.subject not in entity_map:
            entity_map[ef.subject] = get_or_create_entity(driver, ef.subject)

        subject_entity = entity_map[ef.subject]

        # Resolve object if entity
        if ef.object_type == "entity":
            if ef.object not in entity_map:
                entity_map[ef.object] = get_or_create_entity(driver, ef.object)
            obj_entity = entity_map[ef.object]
            obj_value = obj_entity["name"]
        else:
            obj_entity = None
            obj_value = ef.object

        statement = generate_statement(subject_entity["name"], ef.predicate, obj_value)
        now = datetime.now(timezone.utc)
        valid_at = ef.valid_at or now
        valid_at_inferred = ef.valid_at is None

        fact_id = _create_fact(
            driver,
            predicate=ef.predicate,
            statement=statement,
            functional=is_functional(ef.predicate),
            certainty=ef.certainty,
            assertion_source=ef.assertion_source,
            valid_at=valid_at,
            valid_at_inferred=valid_at_inferred,
            namespace=episode.namespace,
            subject_id=subject_entity["id"],
            object_value=obj_value if not obj_entity else None,
            object_entity_id=obj_entity["id"] if obj_entity else None,
            episode_id=episode.id,
            negation=ef.negation,
        )

        # Apply supersession logic
        apply_supersession(
            driver,
            subject_id=subject_entity["id"],
            predicate=ef.predicate,
            new_fact_id=fact_id,
            new_object=obj_value,
            new_valid_at=valid_at,
            namespace=episode.namespace,
            negation=ef.negation,
        )
        facts_written += 1

    return {"episode_id": episode.id, "status": "processed", "facts_written": facts_written}


def _create_episode_node(driver: Driver, episode: Episode):
    query = """
    CREATE (e:Episode {
        id: $id,
        content: $content,
        content_type: $content_type,
        source: $source,
        namespace: $namespace,
        created_at: $created_at
    })
    """
    with driver.session() as session:
        session.run(
            query,
            id=episode.id,
            content=episode.content,
            content_type=episode.content_type,
            source=json.dumps(episode.source),
            namespace=episode.namespace,
            created_at=episode.created_at.isoformat(),
        ).consume()


def _create_fact(
    driver: Driver,
    predicate: str,
    statement: str,
    functional: bool,
    certainty: str,
    assertion_source: str,
    valid_at: datetime,
    valid_at_inferred: bool,
    namespace: str,
    subject_id: str,
    object_value: Optional[str],
    object_entity_id: Optional[str],
    episode_id: str,
    negation: bool,
) -> str:
    """Create Fact node with edges. Returns fact_id.""""
    import uuid
    fact_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    query = """
    CREATE (f:Fact {
        id: $id,
        predicate: $predicate,
        statement: $statement,
        functional: $functional,
        certainty: $certainty,
        epistemic_state: 'active',
        assertion_source: $assertion_source,
        valid_at: $valid_at,
        valid_at_inferred: $valid_at_inferred,
        invalid_at: null,
        recorded_at: $recorded_at,
        namespace: $namespace,
        negation: $negation
    })
    WITH f
    MATCH (s:Entity {id: $subject_id})
    CREATE (f)-[:SUBJECT]->(s)
    """""

    with driver.session() as session:
        session.run(
            query,
            id=fact_id,
            predicate=predicate,
            statement=statement,
            functional=functional,
            certainty=certainty,
            assertion_source=assertion_source,
            valid_at=valid_at.isoformat(),
            valid_at_inferred=valid_at_inferred,
            recorded_at=now.isoformat(),
            namespace=namespace,
            subject_id=subject_id,
            negation=negation,
        ).consume()

        # Object edge
        if object_entity_id:
            obj_query = """
            MATCH (f:Fact {id: $fact_id}), (o:Entity {id: $obj_id})
            CREATE (f)-[:OBJECT]->(o)
            """""
            session.run(obj_query, fact_id=fact_id, obj_id=object_entity_id).consume()
        elif object_value is not None:
            obj_query = """
            MATCH (f:Fact {id: $fact_id})
            CREATE (l:Literal {value: $value, type: 'string'})
            CREATE (f)-[:OBJECT]->(l)
            """""
            session.run(obj_query, fact_id=fact_id, value=object_value).consume()

        # Sourced from episode
        ep_query = """
            MATCH (f:Fact {id: $fact_id}), (e:Episode {id: $ep_id})
            CREATE (f)-[:SOURCED_FROM]->(e)
            """""
        session.run(ep_query, fact_id=fact_id, ep_id=episode_id).consume()

    return fact_id
