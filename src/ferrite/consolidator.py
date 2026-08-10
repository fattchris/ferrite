"""Observation consolidation: synthesize higher-level beliefs from raw facts.

Per spec §3.5 (A6, A7, A8, A16):

Consolidation grouping (A6):
- Group key: (canonical entity, predicate, namespace) — not shared-entity
- Group size cap: 20 facts; overflow splits by recency
- No community detection — deterministic, cheap, testable

Concurrency (A16):
- Redis set 'pending_consolidation' of group keys
- Single dedicated consolidation consumer drains the set
- No lock contention, natural dedup of group keys

Update, not overwrite:
- New evidence SUPPORTS observation → increment proof_count,
  append to evidence_refs, update summary
- New evidence CONTRADICTS → create CONTRADICTS edge,
  flag observation as contradicted
- New evidence EXTENDS → update summary, keep old version
  (old observation gets invalid_at, new with SUPERSEDES link)

Staleness scoping (A7):
- staleness is scoped to the consolidation group, not the entity
- A new fact stales only observations whose group key it belongs to
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from neo4j import Driver

from .retry import retry

logger = logging.getLogger(__name__)

GROUP_CAP = 20  # Maximum facts per consolidation group (A6)


def _group_key(entity_name: str, predicate: str, namespace: str) -> str:
    """Compute the consolidation group key (A6).

    Group key is (canonical entity, predicate, namespace).
    Hub entities get many small topical observations, not one mega-cluster.
    """
    return f"{entity_name}|{predicate}|{namespace}"


def get_pending_groups(redis_client) -> set[str]:
    """Get all pending consolidation group keys from Redis (A16).

    Uses a Redis set 'pending_consolidation' — extraction workers push
    group keys, single consumer drains the set.
    """
    if redis_client is None:
        return set()
    try:
        return {g.decode() if isinstance(g, bytes) else g
                for g in redis_client.smembers("pending_consolidation")}
    except Exception as e:
        logger.warning("Failed to get pending groups: %s", e)
        return set()


def enqueue_consolidation(redis_client, group_key: str) -> None:
    """Push a group key onto the pending consolidation set (A16)."""
    if redis_client is None:
        return
    try:
        redis_client.sadd("pending_consolidation", group_key)
    except Exception as e:
        logger.warning("Failed to enqueue consolidation: %s", e)


def dequeue_consolidation(redis_client, group_key: str) -> None:
    """Remove a group key from the pending set after processing."""
    if redis_client is None:
        return
    try:
        redis_client.srem("pending_consolidation", group_key)
    except Exception as e:
        logger.warning("Failed to dequeue consolidation: %s", e)


def _get_group_facts(
    driver: Driver,
    entity_name: str,
    predicate: str,
    namespace: str,
    limit: int = GROUP_CAP,
) -> list[dict]:
    """Get all active facts for a consolidation group."""
    with driver.session() as s:
        result = s.run(
            """
            MATCH (e:Entity {name: $entity_name})<-[:SUBJECT]-(f:Fact)
            WHERE f.predicate = $predicate
              AND f.namespace = $namespace
              AND f.epistemic_state = 'active'
            OPTIONAL MATCH (f)-[:OBJECT]->(obj)
            RETURN f.id AS id,
                   f.statement AS statement,
                   f.predicate AS predicate,
                   f.certainty AS certainty,
                   f.assertion_source AS assertion_source,
                   f.valid_at AS valid_at,
                   f.recorded_at AS recorded_at,
                   COALESCE(obj.name, obj.value) AS object_value
            ORDER BY f.recorded_at DESC
            LIMIT $limit
            """,
            entity_name=entity_name,
            predicate=predicate,
            namespace=namespace,
            limit=limit,
        )
        return [dict(r) for r in result]


def _get_existing_observation(
    driver: Driver,
    entity_name: str,
    predicate: str,
    namespace: str,
) -> Optional[dict]:
    """Get the most recent active observation for a group."""
    with driver.session() as s:
        result = s.run(
            """
            MATCH (e:Entity {name: $entity_name})<-[:SUBJECT]-(o:Observation)
            WHERE o.predicate = $predicate
              AND o.namespace = $namespace
              AND o.epistemic_state = 'active'
            RETURN o.id AS id,
                   o.summary AS summary,
                   o.proof_count AS proof_count,
                   o.evidence_refs AS evidence_refs,
                   o.epistemic_state AS epistemic_state
            ORDER BY o.recorded_at DESC
            LIMIT 1
            """,
            entity_name=entity_name,
            predicate=predicate,
            namespace=namespace,
        )
        record = result.single()
        return dict(record) if record else None


@retry(max_attempts=3, backoff_base=0.5)
def _create_observation(
    driver: Driver,
    entity_name: str,
    predicate: str,
    namespace: str,
    summary: str,
    facts: list[dict],
    episode_id: Optional[str] = None,
) -> str:
    """Create a new Observation node with SUPPORTS edges to evidence facts."""
    obs_id = str(uuid.uuid4())
    fact_ids = [f["id"] for f in facts]
    now = datetime.now(timezone.utc).isoformat()

    with driver.session() as s:
        # Create the Observation node
        s.run(
            """
            CREATE (o:Observation {
                id: $id,
                summary: $summary,
                predicate: $predicate,
                namespace: $namespace,
                proof_count: $proof_count,
                evidence_refs: $evidence_refs,
                epistemic_state: 'active',
                recorded_at: datetime($now)
            })
            """,
            id=obs_id,
            summary=summary,
            predicate=predicate,
            namespace=namespace,
            proof_count=len(facts),
            evidence_refs=fact_ids,
            now=now,
        ).consume()

        # Link observation to entity as SUBJECT
        s.run(
            """
            MATCH (o:Observation {id: $obs_id}), (e:Entity {name: $entity_name})
            CREATE (o)-[:SUBJECT]->(e)
            """,
            obs_id=obs_id,
            entity_name=entity_name,
        ).consume()

        # Create SUPPORTS edges to evidence facts
        for fid in fact_ids:
            s.run(
                """
                MATCH (o:Observation {id: $obs_id}), (f:Fact {id: $fact_id})
                CREATE (o)-[:SUPPORTS]->(f)
                """,
                obs_id=obs_id,
                fact_id=fid,
            ).consume()

        # Link to episode if provided
        if episode_id:
            s.run(
                """
                MATCH (o:Observation {id: $obs_id}), (ep:Episode {id: $ep_id})
                CREATE (o)-[:SOURCED_FROM]->(ep)
                """,
                obs_id=obs_id,
                ep_id=episode_id,
            ).consume()

    logger.info("Created observation %s for (%s, %s, %s): %d facts",
                obs_id, entity_name, predicate, namespace, len(facts))
    return obs_id


@retry(max_attempts=3, backoff_base=0.5)
def _update_observation(
    driver: Driver,
    obs_id: str,
    summary: str,
    new_facts: list[dict],
) -> None:
    """Update an existing observation with new supporting evidence."""
    new_ids = [f["id"] for f in new_facts]

    with driver.session() as s:
        # Get current evidence refs
        result = s.run(
            "MATCH (o:Observation {id: $id}) "
            "RETURN o.evidence_refs AS refs, o.proof_count AS count",
            id=obs_id,
        )
        record = result.single()
        if not record:
            return

        current_refs = record["refs"] or []
        current_count = record["count"] or 0
        updated_refs = list(set(current_refs + new_ids))
        new_count = current_count + len(new_facts)

        # Update observation
        s.run(
            """
            MATCH (o:Observation {id: $id})
            SET o.summary = $summary,
                o.evidence_refs = $refs,
                o.proof_count = $count
            """,
            id=obs_id,
            summary=summary,
            refs=updated_refs,
            count=new_count,
        ).consume()

        # Create SUPPORTS edges for new facts
        for fid in new_ids:
            s.run(
                """
                MATCH (o:Observation {id: $obs_id}), (f:Fact {id: $fact_id})
                MERGE (o)-[:SUPPORTS]->(f)
                """,
                obs_id=obs_id,
                fact_id=fid,
            ).consume()

    logger.info("Updated observation %s: +%d facts (total: %d)",
                obs_id, len(new_facts), new_count)


@retry(max_attempts=3, backoff_base=0.5)
def _flag_contradiction(
    driver: Driver,
    obs_id: str,
    new_facts: list[dict],
    summary: str,
) -> None:
    """Flag an observation as contradicted by new evidence (A6)."""
    new_ids = [f["id"] for f in new_facts]

    with driver.session() as s:
        # Flag the observation
        s.run(
            """
            MATCH (o:Observation {id: $id})
            SET o.epistemic_state = 'contradicted'
            """,
            id=obs_id,
        ).consume()

        # Create CONTRADICTS edges from new facts to observation
        for fid in new_ids:
            s.run(
                """
                MATCH (f:Fact {id: $fact_id}), (o:Observation {id: $obs_id})
                CREATE (f)-[:CONTRADICTS]->(o)
                """,
                fact_id=fid,
                obs_id=obs_id,
            ).consume()

    logger.warning("Observation %s contradicted by %d new facts",
                   obs_id, len(new_facts))


@retry(max_attempts=3, backoff_base=0.5)
def _supersede_observation(
    driver: Driver,
    old_obs_id: str,
    entity_name: str,
    predicate: str,
    namespace: str,
    summary: str,
    facts: list[dict],
) -> str:
    """Create a new observation that supersedes the old one."""
    now = datetime.now(timezone.utc).isoformat()

    # Invalidate old observation
    with driver.session() as s:
        s.run(
            """
            MATCH (o:Observation {id: $id})
            SET o.epistemic_state = 'superseded',
                o.invalid_at = datetime($now)
            """,
            id=old_obs_id,
            now=now,
        ).consume()

    # Create new observation
    new_id = _create_observation(
        driver, entity_name, predicate, namespace, summary, facts
    )

    # Create SUPERSEDES edge
    with driver.session() as s:
        s.run(
            """
            MATCH (new:Observation {id: $new_id}), (old:Observation {id: $old_id})
            CREATE (new)-[:SUPERSEDES]->(old)
            """,
            new_id=new_id,
            old_id=old_obs_id,
        ).consume()

    logger.info("Observation %s superseded by %s", old_obs_id, new_id)
    return new_id


def _generate_summary(
    entity_name: str,
    predicate: str,
    facts: list[dict],
    llm_client,
    disposition: Optional[dict] = None,
) -> str:
    """Generate a summary of facts using the LLM.

    Disposition traits (§3.7) shape how the consolidation LLM summarizes:
    - skepticism (1-5): higher = require more evidence
    - literalism (1-5): higher = stick to exact facts
    - empathy (1-5): higher = preserve user sentiment
    """
    # Build fact list for prompt
    fact_lines = []
    for f in facts:
        src = f.get("assertion_source", "unknown")
        cert = f.get("certainty", "stated")
        stmt = f["statement"]
        fact_lines.append(f"- [{src}/{cert}] {stmt}")

    facts_text = "\n".join(fact_lines)

    # Apply disposition traits
    disp_text = ""
    if disposition:
        sk = disposition.get("skepticism", 3)
        li = disposition.get("literalism", 3)
        em = disposition.get("empathy", 3)
        disp_text = (
            f"\nDisposition: skepticism={sk}, literalism={li}, empathy={em}.\n"
            f"- skepticism: {'require strong evidence' if sk > 3 else 'moderate threshold'}\n"
            f"- literalism: {'exact facts' if li > 3 else 'allow inference'}\n"
            f"- empathy: {'preserve sentiment' if em > 3 else 'factual focus'}\n"
        )

    system_prompt = f"""\
You are an observation synthesis engine. Given a set of raw facts about an entity,
create a concise summary that captures the consolidated belief.

{disp_text}
Rules:
1. Summarize the facts into 1-3 sentences.
2. Note any contradictions or changes over time.
3. Cite evidence count (e.g. "3 facts support this").
4. Do not add information not present in the facts.
5. Return ONLY the summary text, no preamble.
"""

    user_prompt = f"""\
Entity: {entity_name}
Predicate: {predicate}

Facts:
{facts_text}

Synthesize these facts into a single observation summary."""

    try:
        summary = llm_client(system_prompt, user_prompt)
        return summary.strip()
    except Exception as e:
        logger.warning("LLM summary generation failed: %s", e)
        # Fallback: simple concatenation
        return f"{entity_name} {predicate}: " + "; ".join(
            f.get("object_value", f.get("statement", "")) for f in facts[:5]
        )


def consolidate_group(
    driver: Driver,
    entity_name: str,
    predicate: str,
    namespace: str,
    llm_client,
    redis_client=None,
    disposition: Optional[dict] = None,
) -> Optional[str]:
    """Consolidate facts for a single group into or against an observation.

    This is the single-consumer operation (A16). Called by the consolidation
    consumer after draining the pending_consolidation set.

    Returns the observation ID if created/updated, None if no work.
    """
    group_key = _group_key(entity_name, predicate, namespace)

    # Get all active facts for this group
    facts = _get_group_facts(driver, entity_name, predicate, namespace)
    if not facts:
        logger.debug("No facts for group %s — skipping", group_key)
        return None

    # Check for existing observation
    existing = _get_existing_observation(driver, entity_name, predicate, namespace)

    # Check if facts contradict existing observation
    object_values = {f.get("object_value", "") for f in facts}
    has_contradiction = len(object_values) > 1 and predicate in (
        # Functional predicates where different values = contradiction
        "works_at", "version_is", "runs_on", "has_role",
        "has_version", "runs_model", "deployed_on", "head_node_of",
    )

    if existing:
        existing_refs = set(existing.get("evidence_refs") or [])
        new_facts = [f for f in facts if f["id"] not in existing_refs]
        all_facts = facts
    else:
        new_facts = facts
        all_facts = facts

    # Generate summary from all facts
    summary = _generate_summary(
        entity_name, predicate, all_facts, llm_client, disposition
    )

    obs_id = None

    if not existing:
        # Create new observation
        obs_id = _create_observation(
            driver, entity_name, predicate, namespace,
            summary, all_facts,
        )
    elif has_contradiction and new_facts:
        # Contradiction detected — flag and create new observation
        _flag_contradiction(driver, existing["id"], new_facts, summary)
        obs_id = _supersede_observation(
            driver, existing["id"], entity_name, predicate,
            namespace, summary, all_facts,
        )
    elif new_facts:
        # Supporting evidence — update existing observation
        updated_summary = _generate_summary(
            entity_name, predicate, all_facts, llm_client, disposition
        )
        _update_observation(driver, existing["id"], updated_summary, new_facts)
        obs_id = existing["id"]
    else:
        # No new facts — skip
        logger.debug("No new facts for group %s — skipping", group_key)
        return None

    # Dequeue from Redis
    if redis_client:
        dequeue_consolidation(redis_client, group_key)

    return obs_id


def consolidate_pending(
    driver: Driver,
    llm_client,
    redis_client=None,
    disposition: Optional[dict] = None,
) -> int:
    """Drain the pending consolidation queue (A16).

    Single dedicated consumer: drain the Redis set, consolidate each group.
    Returns count of groups consolidated.
    """
    pending = get_pending_groups(redis_client)
    if not pending:
        return 0

    count = 0
    for group_key in pending:
        # Parse group key: entity|predicate|namespace
        parts = group_key.split("|", 2)
        if len(parts) != 3:
            logger.warning("Malformed group key: %s", group_key)
            dequeue_consolidation(redis_client, group_key)
            continue

        entity_name, predicate, namespace = parts
        try:
            result = consolidate_group(
                driver, entity_name, predicate, namespace,
                llm_client, redis_client, disposition,
            )
            if result:
                count += 1
        except Exception as e:
            logger.error("Consolidation failed for %s: %s", group_key, e)

    logger.info("Consolidated %d/%d groups", count, len(pending))
    return count
