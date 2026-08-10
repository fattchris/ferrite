"""Ingestion pipeline: queue, extract, canonicalize, write to Neo4j.

Architecture (§7.1):
    Agent → MCP store() → Redis Queue → In-proc Consumer → LLM Extraction
                             ↓                              ↓
                        LRU Index                     Canonicalize (A2)
                        (1000 items)                       ↓
                        5 min TTL                   Neo4j Write
                                                        ↓
                                                Consolidation Queue (A16)
                                                (single consumer)
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Callable, Optional

import redis
from .db import close_driver, get_driver
from .canonicalize import resolve_entity
from .extractor import extract, normalize_literal
from .models import Episode, ExtractedEntity, ExtractedFact, ExtractionResult, FactBase
from .quality_gates import assertion_gate, should_consolidate_fact
from .retry import retry
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
FAILED_QUEUE_KEY = "ferrite:failed_queue"
DEAD_LETTER_KEY = "ferrite:dead_letter"
MAX_RETRIES = 3
CONSOLIDATION_QUEUE_KEY = "ferrite:consolidation:queue"


class _NullCtx:
    """Context manager that yields the given session without closing it."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *args):
        pass

# LRU index for read-your-own-writes consistency (§6.4, A9)
# {episode_id, raw_content, queued_at} — 1000 items, 5 min TTL
_LRU_MAXSIZE = 1000
_LRU_TTL_SECONDS = 300  # 5 minutes


class ReadYourOwnWritesLRU:
    """LRU cache for recently stored episodes (§6.4, A9).

    search() checks this cache and merges hits into results flagged
    pending_ingestion, so a search immediately after store() finds
    the just-stored content even before the queue consumer processes it.
    """

    def __init__(self, maxsize: int = _LRU_MAXSIZE, ttl: int = _LRU_TTL_SECONDS):
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl
        self._lock = threading.Lock()

    def put(self, episode_id: str, raw_content: str) -> None:
        """Add an episode to the LRU."""
        with self._lock:
            if episode_id in self._cache:
                self._cache.move_to_end(episode_id)
            self._cache[episode_id] = {
                "raw_content": raw_content,
                "queued_at": time.time(),
            }
            # Evict oldest if over capacity
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def search(self, query: str) -> list[dict]:
        """Simple keyword-overlap scoring over LRU content (§6.4).

        Returns hits flagged as pending_ingestion.
        """
        results: list[dict] = []
        query_lower = query.lower()
        query_terms = set(query_lower.split())

        with self._lock:
            now = time.time()
            expired_keys: list[str] = []
            for ep_id, entry in self._cache.items():
                # Check TTL
                if now - entry["queued_at"] > self._ttl:
                    expired_keys.append(ep_id)
                    continue

                content = entry["raw_content"].lower()
                # Simple keyword overlap scoring
                content_terms = set(content.split())
                overlap = len(query_terms & content_terms)
                if overlap > 0:
                    score = overlap / max(len(query_terms), 1)
                    results.append(
                        {
                            "id": f"pending:{ep_id}",
                            "statement": entry["raw_content"][:500],
                            "certainty": 0.0,
                            "source": "pending_ingestion",
                            "valid_at": "",
                            "pending_ingestion": True,
                            "score": score,
                        }
                    )

            # Cleanup expired
            for key in expired_keys:
                self._cache.pop(key, None)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def remove(self, episode_id: str) -> None:
        """Remove an episode from the LRU after it's been ingested."""
        with self._lock:
            self._cache.pop(episode_id, None)


# Global LRU instance
_lru_index = ReadYourOwnWritesLRU()


def get_lru() -> ReadYourOwnWritesLRU:
    """Get the global LRU index."""
    return _lru_index


class IngestionPipeline:
    """Manages the ingestion queue and processing pipeline."""

    def __init__(
        self,
        redis_url: str,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        embedding_func: Optional[Callable[[str], Optional[list[float]]]] = None,
        llm_client: Optional[Callable[[str, str], str]] = None,
    ):
        self.redis_client = redis.from_url(redis_url)
        self.driver = get_driver()
        self.embedding_func = embedding_func
        self.llm_client = llm_client

    def enqueue(self, episode: Episode) -> str:
        """Queue an episode for ingestion. Returns the episode_id.

        Store is always async through the queue interface (§6.4, A9).
        LRU index keeps {episode_id, raw_content} for RYOW consistency.
        Saves the original content to the file repository (§10.4).
        """
        # Save original content to file repository
        try:
            from .file_repo import save_content
            episode.source_file = save_content(
                content=episode.content,
                content_type=episode.content_type,
                source=episode.source,
                episode_id=episode.id,
            )
            logger.info(f"Saved source file for episode {episode.id}: {episode.source_file}")
        except Exception as e:
            logger.warning(f"Failed to save source file for episode {episode.id}: {e}")

        episode_data = episode.model_dump_json()
        self.redis_client.hset(
            f"{EPISODE_KEY_PREFIX}{episode.id}", "data", episode_data
        )
        self.redis_client.lpush(QUEUE_KEY, episode.id)
        # Add to LRU for read-your-own-writes (§6.4)
        _lru_index.put(episode.id, episode.content)
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
        # Remove from LRU after ingestion (§6.4)
        _lru_index.remove(episode_id)
        return episode_id

    async def start_consumer(self, poll_interval: float = 1.0) -> None:
        """In-proc async queue consumer (§7.1, §6.4).

        Runs inside the API container. Polls Redis queue and processes
        episodes as they arrive. Single consumer for MVP.
        Also drains the failed queue (F-5 fix) with priority.
        """
        logger.info("Starting in-proc ingestion consumer")
        while True:
            try:
                # Check failed queue first (retry with priority, F-5 fix)
                episode_id = self.redis_client.rpop(FAILED_QUEUE_KEY)
                if not episode_id:
                    episode_id = self.redis_client.rpop(QUEUE_KEY)
                if episode_id is None:
                    await asyncio.sleep(poll_interval)
                    continue

                episode_id = (
                    episode_id.decode()
                    if isinstance(episode_id, bytes)
                    else episode_id
                )
                await asyncio.get_event_loop().run_in_executor(
                    None, self.process_episode, episode_id
                )
                _lru_index.remove(episode_id)
            except asyncio.CancelledError:
                logger.info("Ingestion consumer cancelled")
                break
            except Exception as e:
                logger.error(f"Consumer error: {e}", exc_info=True)
                await asyncio.sleep(poll_interval)

    def process_episode(self, episode_id: str) -> None:
        """Process a single episode: extract -> canonicalize -> write to Neo4j.

        On failure, pushes to failed_queue with retry count (F-5 fix).
        After MAX_RETRIES, moves to dead_letter for manual inspection.
        """
        # Load episode from Redis
        raw = self.redis_client.hget(f"{EPISODE_KEY_PREFIX}{episode_id}", "data")
        if raw is None:
            logger.error(f"Episode {episode_id} not found in Redis")
            return

        raw = raw.decode() if isinstance(raw, bytes) else raw
        episode = Episode.model_validate_json(raw)

        logger.info(f"Processing episode {episode.id}")

        try:
            # Step 1: Extract entities and facts via LLM (with retry, F-7 fix)
            try:
                extraction = extract(episode.content, self.llm_client)
            except (ValueError, KeyError) as extract_err:
                if self.llm_client is not None:
                    logger.warning(
                        f"Extraction failed ({extract_err}), "
                        f"retrying with truncated content"
                    )
                    truncated = episode.content[:2000]
                    extraction = extract(truncated, self.llm_client)
                else:
                    raise

            if extraction is None:
                raise RuntimeError("Extraction returned None after retries")

            # Step 2: Canonicalize entities
            entity_cache: dict[str, object] = {}
            for ent_data in extraction.entities:
                name = ent_data.name
                if name not in entity_cache:
                    entity = resolve_entity(
                        self.driver,
                        name,
                        self.embedding_func,
                        entity_type=ent_data.type,
                        summary=ent_data.summary,
                    )
                    if entity is not None:
                        entity_cache[name] = entity
                    else:
                        logger.warning(f"Entity resolution returned None for: {name}")

            # Also canonicalize subjects and objects referenced in facts
            for fact_data in extraction.facts:
                subj_name = fact_data.subject
                if subj_name not in entity_cache:
                    entity = resolve_entity(
                        self.driver, subj_name, self.embedding_func
                    )
                    if entity is not None:
                        entity_cache[subj_name] = entity

                if fact_data.object_type == "entity":
                    obj_name = fact_data.object
                    if obj_name not in entity_cache:
                        entity = resolve_entity(
                            self.driver, obj_name, self.embedding_func
                        )
                        if entity is not None:
                            entity_cache[obj_name] = entity

            # Step 3: Write facts to Neo4j with temporal logic
            with self.driver.session() as session:
                for fact_data in extraction.facts:
                    # Assertion gate (§7.2.1): skip facts with invalid assertion_source
                    if not assertion_gate(fact_data.model_dump()):
                        logger.warning(f"Skipping fact that failed assertion gate: {fact_data}")
                        continue
                    self._write_fact_with_temporal(
                        episode, fact_data, entity_cache, session
                    )

            # Step 4: Enqueue consolidation groups for newly written facts (A16)
            try:
                from .consolidator import _group_key, enqueue_consolidation
                for fact_data in extraction.facts:
                    # model-sourced facts excluded from consolidation (§7.2.1)
                    if not should_consolidate_fact(fact_data.model_dump()):
                        continue
                    subj_name = fact_data.subject
                    predicate = fact_data.predicate
                    namespace = episode.namespace or "shared"
                    gk = _group_key(subj_name, predicate, namespace)
                    enqueue_consolidation(self.redis_client, gk)
            except Exception as e:
                logger.debug("Consolidation enqueue failed: %s", e)

            logger.info(f"Completed processing episode {episode.id}")

        except Exception as e:
            # DLQ logic (F-5 fix): retry with backoff, then dead letter
            retry_count = self.redis_client.hget(
                f"{EPISODE_KEY_PREFIX}{episode_id}", "retries"
            )
            retries = int(retry_count) if retry_count else 0

            if retries < MAX_RETRIES:
                self.redis_client.hset(
                    f"{EPISODE_KEY_PREFIX}{episode_id}", "retries", retries + 1
                )
                self.redis_client.hset(
                    f"{EPISODE_KEY_PREFIX}{episode_id}", "last_error", str(e)
                )
                # Push back to failed queue for retry
                self.redis_client.lpush(FAILED_QUEUE_KEY, episode_id)
                logger.warning(
                    f"Episode {episode_id} failed (attempt {retries + 1}/{MAX_RETRIES}): {e}"
                )
            else:
                # Move to dead letter queue
                dlq_entry = json.dumps({
                    "episode_id": episode_id,
                    "content": episode.content[:500],
                    "error": str(e),
                    "retries": retries,
                })
                self.redis_client.lpush(DEAD_LETTER_KEY, dlq_entry)
                logger.error(
                    f"Episode {episode_id} moved to dead letter queue "
                    f"after {MAX_RETRIES} retries: {e}"
                )

    def _write_fact_with_temporal(
        self,
        episode: Episode,
        fact_data: ExtractedFact,
        entity_cache: dict,
        session = None,
    ) -> None:
        """Write a single fact to Neo4j, applying temporal logic."""
        predicate = fact_data.predicate
        if not is_valid_predicate(predicate):
            logger.warning(f"Skipping fact with invalid predicate: {predicate}")
            return

        subject_entity = entity_cache.get(fact_data.subject)
        if subject_entity is None:
            logger.warning(
                f"Subject entity not found for: {fact_data.subject} — skipping fact"
            )
            return

        # Determine valid_at
        if fact_data.valid_at:
            try:
                valid_at = datetime.fromisoformat(fact_data.valid_at)
                valid_at_inferred = False
            except (ValueError, TypeError):
                valid_at = episode.recorded_at
                valid_at_inferred = True
        else:
            valid_at = episode.recorded_at
            valid_at_inferred = True

        # Determine object
        object_type = fact_data.object_type
        object_value = fact_data.object
        if object_type == "literal":
            object_value = normalize_literal(object_value)

        # Build the canonical statement
        statement = f"{fact_data.subject} {predicate} {object_value}"

        # Create the Fact object
        fact = FactBase(
            predicate=predicate,
            statement=statement,
            functional=is_functional(predicate),
            certainty=fact_data.certainty,
            assertion_source=fact_data.assertion_source,
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
                    fact, subject_entity, object_value, object_type, episode, session
                )
                apply_supersession(
                    self.driver, old_fact["id"], fact.id, valid_at
                )
                return

        # Check for contradiction (negation flag)
        if fact_data.negation:
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
                    fact, subject_entity, object_value, object_type, episode, session
                )
                apply_contradiction(self.driver, existing_fact_id, fact.id)
                return

        # Normal write
        self._write_fact(fact, subject_entity, object_value, object_type, episode, session)

    @retry(max_attempts=5, backoff_base=1.0)
    def _write_fact(
        self,
        fact: FactBase,
        subject_entity,
        object_value: str,
        object_type: str,
        episode: Episode,
        session = None,
    ) -> None:
        """Write a Fact node to Neo4j with SUBJECT, OBJECT, SOURCED_FROM edges."""
        if session is None:
            session_ctx = self.driver.session()
        else:
            session_ctx = _NullCtx(session)

        with session_ctx as session:
            # Create Episode node if not exists
            session.run(
                """
                MERGE (ep:Episode {id: $episode_id})
                SET ep.content = $content,
                    ep.content_type = $content_type,
                    ep.source = $source,
                    ep.source_file = $source_file,
                    ep.namespace = $namespace,
                    ep.recorded_at = $recorded_at
                """,
                episode_id=episode.id,
                content=episode.content,
                content_type=episode.content_type,
                source=json.dumps(episode.source),
                source_file=episode.source_file,
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
                MATCH (f:Fact {id: $fact_id})
                MATCH (e:Entity {id: $entity_id})
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

            # Embed the fact statement (§7.3 F-2: fact statements are embedded)
            embedding_val = None
            if self.embedding_func is not None:
                try:
                    embedding_val = self.embedding_func(fact.statement)
                except Exception as e:
                    logger.warning("Embedding failed for fact %s: %s", fact.id, e)

            if embedding_val is not None:
                session.run(
                    "MATCH (f:Fact {id: $fact_id}) SET f.embedding = $embedding",
                    fact_id=fact.id,
                    embedding=embedding_val,
                )

            # Link provenance
            session.run(
                """
                MATCH (f:Fact {id: $fact_id})
                MATCH (ep:Episode {id: $episode_id})
                CREATE (f)-[:SOURCED_FROM]->(ep)
                """,
                fact_id=fact.id,
                episode_id=episode.id,
            )

            # Create observation and link
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
        close_driver()
        self.redis_client.close()
