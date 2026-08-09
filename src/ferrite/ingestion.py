"""Ingestion pipeline: queue, extract, canonicalize, write to Neo4j."""

import json
import logging
from datetime import datetime
from typing import Callable, Optional

import redis
from neo4j import GraphDatabase

from .canonicalize import resolve_entity
from .extractor import extract, normalize_literal
from .models import Episode, FactBase
from .temporal import (
    apply_contradiction,
    apply_supersession,
    detect_contradiction,
    detect_supersession,
)
from .vocab import is_functional, is_valid_predicate

logger = logging.getLogger(__name__)

QUEUE_KEY = "ferrite:ingestion:queue"
EPISODE_KEY_PREFIX = "ferrite:episode:"


class IngestionPipeline:
    """Manages the ingestion queue and processing pipeline."""

    def __init__(
        self,
        redis_url: str,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        embedding_func: Optional[Callable] = None,
        llm_client: Optional[Callable] = None,
    ):
        self.redis_client = redis.from_url(redis_url)
        self.driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        self.embedding_func = embedding_func
        self.llm_client = llm_client

    def enqueue(self, episode: Episode) -> str:
        """Queue an episode for ingestion. Returns the episode_id."""
        episode_data = episode.model_dump_json()
        self.redis_client.hset(
            f"{EPISODE_KEY_PREFIX}{episode.id}", "data", episode_data
        )
        self.redis_client.lpush(QUEUE_KEY, episode.id)
        logger.info(f"Enqueued episode {episode.id}")
        return episode.id

    def get_queue_depth(self) -> int:
        """Return the number of episodes in the queue."""
        return self.redis_client.llen(QUEUE_KEY)

    def process_next(self) -> Optional[str]:
        """Process the next episode in the queue. Returns episode_id or None."""
        episode_id = self.redis_client.rpop(QUEUE_KEY)
        if episode_id is None:
            return None

        episode_id = episode_id.decode() if isinstance(episode_id, bytes) else episode_id
        self.process_episode(episode_id)
        return episode_id

    def process_episode(self, episode_id: str) -> None:
        """Process a single episode: extract -> canonicalize -> write to Neo4j."""
        # Load episode from Redis
        raw = self.redis_client.hget(f"{EPISODE_KEY_PREFIX}{episode_id}", "data")
        if raw is None:
            logger.error(f"Episode {episode_id} not found in Redis")
            return

        raw = raw.decode() if isinstance(raw, bytes) else raw
        episode = Episode.model_validate_json(raw)

        logger.info(f"Processing episode {episode.id}")

        # Step 1: Extract entities and facts via LLM
        extraction = extract(episode.content, self.llm_client)

        # Step 2: Canonicalize entities
        entity_cache: dict[str, object] = {}
        for ent_data in extraction.get("entities", []):
            name = ent_data["name"]
            if name not in entity_cache:
                entity = resolve_entity(
                    self.driver,
                    name,
                    self.embedding_func,
                    entity_type=ent_data.get("type", "entity"),
                    summary=ent_data.get("summary"),
                )
                entity_cache[name] = entity

        # Also canonicalize subjects and objects referenced in facts
        for fact_data in extraction.get("facts", []):
            subj_name = fact_data["subject"]
            if subj_name not in entity_cache:
                entity = resolve_entity(
                    self.driver, subj_name, self.embedding_func
                )
                entity_cache[subj_name] = entity

            if fact_data.get("object_type") == "entity":
                obj_name = fact_data["object"]
                if obj_name not in entity_cache:
                    entity = resolve_entity(
                        self.driver, obj_name, self.embedding_func
                    )
                    entity_cache[obj_name] = entity

        # Step 3: Write facts to Neo4j with temporal logic
        for fact_data in extraction.get("facts", []):
            self._write_fact_with_temporal(episode, fact_data, entity_cache)

        logger.info(f"Completed processing episode {episode.id}")

    def _write_fact_with_temporal(
        self,
        episode: Episode,
        fact_data: dict,
        entity_cache: dict,
    ) -> None:
        """Write a single fact to Neo4j, applying temporal logic."""
        predicate = fact_data["predicate"]
        if not is_valid_predicate(predicate):
            logger.warning(f"Skipping fact with invalid predicate: {predicate}")
            return

        subject_entity = entity_cache.get(fact_data["subject"])
        if subject_entity is None:
            logger.warning(
                f"Subject entity not found for: {fact_data['subject']}"
            )
            return

        # Determine valid_at
        if fact_data.get("valid_at"):
            try:
                valid_at = datetime.fromisoformat(fact_data["valid_at"])
                valid_at_inferred = False
            except (ValueError, TypeError):
                valid_at = episode.recorded_at
                valid_at_inferred = True
        else:
            valid_at = episode.recorded_at
            valid_at_inferred = True

        # Determine object
        object_type = fact_data.get("object_type", "entity")
        object_value = fact_data["object"]
        if object_type == "literal":
            object_value = normalize_literal(object_value)

        # Build the canonical statement
        statement = f"{fact_data['subject']} {predicate} {object_value}"

        # Create the Fact object
        fact = FactBase(
            predicate=predicate,
            statement=statement,
            functional=is_functional(predicate),
            certainty=fact_data.get("certainty", "stated"),
            assertion_source=fact_data.get("assertion_source", "model"),
            valid_at=valid_at,
            valid_at_inferred=valid_at_inferred,
            namespace=episode.namespace,
            recorded_at=episode.recorded_at,
        )

        # Check for supersession (functional predicates only)
        if is_functional(predicate):
            old_fact = detect_supersession(
                self.driver,
                subject_entity.id,
                predicate,
                object_value,
                object_type,
                episode.namespace,
            )
            if old_fact:
                # Write new fact first, then supersede old
                self._write_fact(
                    fact, subject_entity, object_value, object_type, episode
                )
                apply_supersession(
                    self.driver, old_fact["id"], fact.id, valid_at
                )
                return

        # Check for contradiction (negation flag)
        if fact_data.get("negation", False):
            existing_fact_id = detect_contradiction(
                self.driver,
                subject_entity.id,
                predicate,
                object_value,
                True,
                episode.namespace,
            )
            if existing_fact_id:
                self._write_fact(
                    fact, subject_entity, object_value, object_type, episode
                )
                apply_contradiction(self.driver, existing_fact_id, fact.id)
                return

        # Normal write
        self._write_fact(fact, subject_entity, object_value, object_type, episode)

    def _write_fact(
        self,
        fact: FactBase,
        subject_entity,
        object_value: str,
        object_type: str,
        episode: Episode,
    ) -> None:
        """Write a Fact node to Neo4j with SUBJECT, OBJECT, SOURCED_FROM edges."""
        with self.driver.session() as session:
            # Create Episode node if not exists
            session.run(
                """
                MERGE (ep:Episode {id: $episode_id})
                SET ep.content = $content,
                    ep.content_type = $content_type,
                    ep.source = $source,
                    ep.namespace = $namespace,
                    ep.recorded_at = $recorded_at
                """,
                episode_id=episode.id,
                content=episode.content,
                content_type=episode.content_type,
                source=json.dumps(episode.source),
                namespace=episode.namespace,
                recorded_at=episode.recorded_at.isoformat(),
            )

            # Create Fact node
            session.run(
                """
                CREATE (f:Fact {
                    id: $id,
                    predicate: $predicate,
                    statement: $statement,
                    functional: $functional,
                    certainty: $certainty,
                    epistemic_state: $epistemic_state,
                    assertion_source: $assertion_source,
                    valid_at: $valid_at,
                    valid_at_inferred: $valid_at_inferred,
                    invalid_at: $invalid_at,
                    recorded_at: $recorded_at,
                    namespace: $namespace
                })
                """,
                id=fact.id,
                predicate=fact.predicate,
                statement=fact.statement,
                functional=fact.functional,
                certainty=fact.certainty,
                epistemic_state=fact.epistemic_state,
                assertion_source=fact.assertion_source,
                valid_at=fact.valid_at.isoformat(),
                valid_at_inferred=fact.valid_at_inferred,
                invalid_at=None,
                recorded_at=fact.recorded_at.isoformat(),
                namespace=fact.namespace,
            )

            # Link subject
            session.run(
                """
                MATCH (f:Fact {id: $fact_id}), (e:Entity {id: $entity_id})
                CREATE (f)-[:SUBJECT]->(e)
                """,
                fact_id=fact.id,
                entity_id=subject_entity.id,
            )

            # Link object
            if object_type == "entity":
                # Find the object entity and link it
                session.run(
                    """
                    MATCH (f:Fact {id: $fact_id})
                    MATCH (e:Entity {name: $obj_name})
                    WITH f, e
                    CREATE (f)-[:OBJECT]->(e)
                    """,
                    fact_id=fact.id,
                    obj_name=object_value,
                )
            else:
                # Create a literal node
                session.run(
                    """
                    MATCH (f:Fact {id: $fact_id})
                    CREATE (l:Literal {value: $value, type: 'literal'})
                    CREATE (f)-[:OBJECT]->(l)
                    """,
                    fact_id=fact.id,
                    value=object_value,
                )

            # Link provenance
            session.run(
                """
                MATCH (f:Fact {id: $fact_id}), (ep:Episode {id: $episode_id})
                CREATE (f)-[:SOURCED_FROM]->(ep)
                """,
                fact_id=fact.id,
                episode_id=episode.id,
            )

            # Create observation and link
            import uuid
            obs_id = str(uuid.uuid4())
            session.run(
                """
                CREATE (o:Observation {id: $obs_id, episode_id: $episode_id, fact_id: $fact_id})
                WITH o
                MATCH (f:Fact {id: $fact_id})
                CREATE (o)-[:SUPPORTS]->(f)
                """,
                obs_id=obs_id,
                episode_id=episode.id,
                fact_id=fact.id,
            )

        logger.info(f"Wrote fact {fact.id}: {fact.statement}")

    def close(self) -> None:
        """Close connections."""
        self.driver.close()
        self.redis_client.close()
