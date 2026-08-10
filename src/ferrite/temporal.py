"""Temporal logic: supersession and contradiction detection for Facts."""

import logging
from datetime import datetime
from typing import Optional

from neo4j import Driver

from .retry import retry

logger = logging.getLogger(__name__)


def detect_supersession(
    driver: Driver,
    subject_id: str,
    predicate: str,
    new_object: str,
    new_object_type: str,
    namespace: str,
) -> Optional[dict]:
    """Detect if an existing active Fact should be superseded.

    For functional predicates: if there is an existing active Fact with the same
    (subject, predicate) but a different object, the old fact should be superseded.

    Returns the old fact dict (id, object, valid_at) if supersession is needed,
    else None.
    """
    with driver.session() as session:
        result = session.run(
            """
            MATCH (e:Entity {id: $subject_id})<-[:SUBJECT]-(f:Fact)
            OPTIONAL MATCH (f)-[:OBJECT]->(target)
            WHERE f.predicate = $predicate
              AND f.epistemic_state = 'active'
              AND f.invalid_at IS NULL
              AND f.namespace = $namespace
            RETURN f.id AS id, f.statement AS statement, f.valid_at AS valid_at,
                   f.namespace AS namespace,
                   COALESCE(target.name, target.value) AS obj_value
            """,
            subject_id=subject_id,
            predicate=predicate,
            namespace=namespace,
        )
        records = [r for r in result]

    if not records:
        return None

    # For functional predicates, check if any existing fact has a different object
    for record in records:
        existing_object = record["obj_value"]

        if existing_object != new_object:
            logger.info(
                f"Supersession detected: fact {record['id']} has different "
                f"object ('{existing_object}' vs '{new_object}')"
            )
            return {
                "id": record["id"],
                "statement": record["statement"],
                "valid_at": record["valid_at"],
                "namespace": record["namespace"],
            }

    return None


@retry(max_attempts=3, backoff_base=0.5)
def apply_supersession(
    driver: Driver,
    old_fact_id: str,
    new_fact_id: str,
    new_valid_at: datetime,
) -> None:
    """Apply supersession: mark old fact as superseded, create SUPERSEDES edge.

    - Set old_fact.invalid_at = new_fact.valid_at
    - Set old_fact.epistemic_state = 'superseded'
    - Create SUPERSEDES edge from new fact to old fact
    """
    with driver.session() as session:
        session.run(
            """
            MATCH (old:Fact {id: $old_fact_id}), (new:Fact {id: $new_fact_id})
            SET old.invalid_at = $new_valid_at,
                old.epistemic_state = 'superseded'
            CREATE (new)-[:SUPERSEDES]->(old)
            """,
            old_fact_id=old_fact_id,
            new_fact_id=new_fact_id,
            new_valid_at=(
                new_valid_at.isoformat()
                if isinstance(new_valid_at, datetime)
                else new_valid_at
            ),
        )
    logger.info(
        f"Applied supersession: {new_fact_id} supersedes {old_fact_id}, "
        f"invalid_at set to {new_valid_at}"
    )


def detect_contradiction(
    driver: Driver,
    subject_id: str,
    predicate: str,
    new_object: str,
    new_negation: bool,
    namespace: str,
) -> Optional[str]:
    """Detect contradiction: same subject+predicate+object with negation flag.

    If a new fact has negation=true and an existing active fact has the same
    subject+predicate+object (or vice versa), both should be flagged as contradicted.

    Returns the existing fact ID if contradiction is detected, else None.
    """
    with driver.session() as session:
        result = session.run(
            """
            MATCH (e:Entity {id: $subject_id})<-[:SUBJECT]-(f:Fact)
            OPTIONAL MATCH (f)-[:OBJECT]->(target)
            WHERE f.predicate = $predicate
              AND f.epistemic_state = 'active'
              AND f.invalid_at IS NULL
              AND f.namespace = $namespace
            RETURN f.id AS id,
                   COALESCE(target.name, target.value) AS obj_value
            """,
            subject_id=subject_id,
            predicate=predicate,
            namespace=namespace,
        )
        for record in result:
            existing_object = record["obj_value"]

            if existing_object == new_object:
                # Contradiction: same subject+predicate+object with negation
                logger.info(
                    f"Contradiction detected: fact {record['id']} has same "
                    f"object '{new_object}' with negation flag"
                )
                return record["id"]

    return None


@retry(max_attempts=3, backoff_base=0.5)
def apply_contradiction(
    driver: Driver,
    existing_fact_id: str,
    new_fact_id: str,
) -> None:
    """Apply contradiction: flag both facts as contradicted, create CONTRADICTS edge."""
    with driver.session() as session:
        session.run(
            """
            MATCH (existing:Fact {id: $existing_id}), (new:Fact {id: $new_id})
            SET existing.epistemic_state = 'contradicted',
                new.epistemic_state = 'contradicted'
            CREATE (new)-[:CONTRADICTS]->(existing)
            """,
            existing_id=existing_fact_id,
            new_id=new_fact_id,
        )
    logger.info(
        f"Applied contradiction: {new_fact_id} contradicts {existing_fact_id}"
    )


def get_history_as_of_knowledge(
    driver: Driver,
    entity_id: str,
    at_time: datetime,
    namespace: Optional[str] = None,
) -> list[dict]:
    """Query what Ferrite knew about an entity as of a knowledge time.

    Filters by recorded_at <= at_time.
    """
    ns_filter = "AND f.namespace = $namespace" if namespace else ""

    with driver.session() as session:
        result = session.run(
            f"""
            MATCH (e:Entity {{id: $entity_id}})<-[:SUBJECT]-(f:Fact)
            WHERE f.recorded_at <= $at_time
            {ns_filter}
            RETURN f.id AS id, f.statement AS statement, f.predicate AS predicate,
                   f.certainty AS certainty, f.epistemic_state AS epistemic_state,
                   f.valid_at AS valid_at, f.invalid_at AS invalid_at,
                   f.recorded_at AS recorded_at, f.namespace AS namespace
            ORDER BY f.recorded_at DESC
            """,
            entity_id=entity_id,
            at_time=at_time.isoformat() if isinstance(at_time, datetime) else at_time,
            namespace=namespace,
        )
        return [dict(r) for r in result]


def get_history_as_of_world(
    driver: Driver,
    entity_id: str,
    at_time: datetime,
    namespace: Optional[str] = None,
) -> list[dict]:
    """Query what was true about an entity as of a world time.

    Filters by valid_at <= at_time < coalesce(invalid_at, infinity).
    """
    ns_filter = "AND f.namespace = $namespace" if namespace else ""

    with driver.session() as session:
        result = session.run(
            f"""
            MATCH (e:Entity {{id: $entity_id}})<-[:SUBJECT]-(f:Fact)
            WHERE f.valid_at <= $at_time
              AND (f.invalid_at IS NULL OR f.invalid_at > $at_time)
              {ns_filter}
            RETURN f.id AS id, f.statement AS statement, f.predicate AS predicate,
                   f.certainty AS certainty, f.epistemic_state AS epistemic_state,
                   f.valid_at AS valid_at, f.invalid_at AS invalid_at,
                   f.recorded_at AS recorded_at, f.namespace AS namespace
            ORDER BY f.valid_at DESC
            """,
            entity_id=entity_id,
            at_time=at_time.isoformat() if isinstance(at_time, datetime) else at_time,
            namespace=namespace,
        )
        return [dict(r) for r in result]
