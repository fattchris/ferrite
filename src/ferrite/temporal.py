"""Temporal supersession and contradiction detection logic.""""

from datetime import datetime
from typing import Optional

from neo4j import Driver

from ferrite.vocab import is_functional


def apply_supersession(
    driver: Driver,
    subject_id: str,
    predicate: str,
    new_fact_id: str,
    new_object: str,
    new_valid_at: datetime,
    namespace: str,
    negation: bool = False,
) -> Optional[str]:
    """Apply supersession/contradiction logic for a new fact.
    
    Returns:
        "superseded", "contradicted", "coexist", or None.
    """"
    if not is_functional(predicate):
        # Non-functional: coexist, but check for contradictions
        return _check_contradiction(driver, subject_id, predicate, new_object, new_fact_id, namespace, negation)

    # Functional: find active fact with same (subject, predicate)
    query = """
    MATCH (e:Entity {id: $subject_id})<-[:SUBJECT]-(f:Fact {
        predicate: $predicate,
        namespace: $namespace,
        epistemic_state: 'active'
    })
    WHERE f.invalid_at IS NULL
    RETURN f.id AS id, f.statement AS statement,
           [(f)-[:OBJECT]->(o) | coalesce(o.value, o.name)] AS objects
    """
    with driver.session() as session:
        result = session.run(
            query,
            subject_id=subject_id,
            predicate=predicate,
            namespace=namespace
        )
        records = list(result)

    if not records:
        return _check_contradiction(driver, subject_id, predicate, new_object, new_fact_id, namespace, negation)

    for rec in records:
        old_id = rec["id"]
        old_objects = rec["objects"]
        old_object = old_objects[0] if old_objects else ""

        if old_object == new_object and not negation:
            # Same fact, no change needed
            return "coexist"

        # Different object or negation -> supersede old
        update_query = """
        MATCH (f:Fact {id: $old_id})
        SET f.invalid_at = $new_valid_at,
            f.epistemic_state = 'superseded'
        WITH f
        MATCH (nf:Fact {id: $new_fact_id})
        CREATE (nf)-[:SUPERSEDES]->(f)
        """""
        with driver.session() as session:
            session.run(
                update_query,
                old_id=old_id,
                new_fact_id=new_fact_id,
                new_valid_at=new_valid_at
            ).consume()

    return "superseded"


def _check_contradiction(
    driver: Driver,
    subject_id: str,
    predicate: str,
    new_object: str,
    new_fact_id: str,
    namespace: str,
    negation: bool,
) -> Optional[str]:
    """Check if new fact contradicts existing fact (same pred+obj, negation flag).""""
    if not negation:
        return None

    query = """
    MATCH (e:Entity {id: $subject_id})<-[:SUBJECT]-(f:Fact {
        predicate: $predicate,
        namespace: $namespace,
        epistemic_state: 'active'
    })
    WHERE f.invalid_at IS NULL
    RETURN f.id AS id
    """
    with driver.session() as session:
        result = session.run(
            query,
            subject_id=subject_id,
            predicate=predicate,
            namespace=namespace
        )
        records = list(result)

    for rec in records:
        old_id = rec["id"]
        update_query = """
        MATCH (f:Fact {id: $old_id}), (nf:Fact {id: $new_fact_id})
        SET f.epistemic_state = 'contradicted',
            nf.epistemic_state = 'contradicted'
        CREATE (nf)-[:CONTRADICTS]->(f)
        """""
        with driver.session() as session:
            session.run(update_query, old_id=old_id, new_fact_id=new_fact_id).consume()

    return "contradicted" if records else None
