"""Integration tests for Ferrite — full pipeline against real Neo4j + Redis + LiteLLM.

Tests:
1. Store fact via pipeline → query from Neo4j
2. Entity canonicalization (alias lookup)
3. LLM extraction via LiteLLM → Neo4j write
4. Temporal supersession on real data
5. Temporal queries (as-of-knowledge, as-of-world)
6. Semantic search via fulltext index
"""

import json
import logging
import os
from datetime import datetime

import pytest
import redis
from neo4j import GraphDatabase

from ferrite.canonicalize import (
    create_entity,
    normalize_name,
    resolve_entity,
)
from ferrite.extractor import (
    extract,
)
from ferrite.ingestion import IngestionPipeline
from ferrite.models import Episode
from ferrite.schema import init_schema
from ferrite.temporal import (
    apply_supersession,
    detect_contradiction,
    detect_supersession,
    get_history_as_of_knowledge,
    get_history_as_of_world,
)
from ferrite.vocab import is_valid_predicate

logger = logging.getLogger(__name__)

# --- Configuration ---

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "ferrite123")
REDIS_URL = "redis://localhost:6379"
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-litellm-cd2008b7d243eb3c14a96ef55f80f529")
LITELLM_MODEL = "glm-5.2"

# --- Fixtures ---


@pytest.fixture(scope="session")
def neo4j_driver():
    """Neo4j driver, initialized once per session."""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    init_schema(driver)
    yield driver
    driver.close()


@pytest.fixture(scope="session")
def redis_client():
    """Redis client."""
    r = redis.from_url(REDIS_URL)
    r.ping()
    yield r
    # Cleanup Ferrite keys
    for key in r.keys("ferrite:*"):
        r.delete(key)


@pytest.fixture(autouse=True)
def clean_neo4j(neo4j_driver):
    """Clean all Ferrite data before each test."""
    with neo4j_driver.session() as s:
        s.run(
            "MATCH (n) WHERE n:Fact OR n:Entity OR n:Alias "
            "OR n:Episode OR n:Observation OR n:Literal "
            "DETACH DELETE n"
        )
    yield


def llm_client(system_prompt: str, user_prompt: str) -> str:
    """Call LiteLLM proxy for LLM extraction."""
    import urllib.request

    url = f"{LITELLM_BASE_URL}/chat/completions"
    payload = json.dumps({
        "model": LITELLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LITELLM_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


# --- Test 1: Store fact via pipeline → query from Neo4j ---


def test_store_and_query_fact(neo4j_driver, redis_client):
    """Store a fact through the ingestion pipeline and query it back."""
    pipeline = IngestionPipeline(
        redis_url=REDIS_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
        llm_client=None,  # No LLM — we'll inject facts manually
    )

    # Create an episode with simple text
    episode = Episode(
        content="Alice works at Acme Corp.",
        content_type="text",
        source={"test": "integration"},
    )

    # Instead of using the full pipeline (which needs LLM),
    # directly test the canonicalization + write path
    entity = create_entity(neo4j_driver, "Alice", "entity", "Test entity")

    # Verify entity was created
    with neo4j_driver.session() as s:
        result = s.run("MATCH (e:Entity {name: 'Alice'}) RETURN e.id AS id, e.type AS type")
        record = result.single()
        assert record is not None
        assert record["id"] == entity.id
        assert record["type"] == "entity"

    # Create a fact manually
    from ferrite.models import FactBase

    fact = FactBase(
        predicate="works_at",
        statement="alice works_at acme corp",
        functional=True,
        certainty="stated",
        assertion_source="model",
        valid_at=datetime.utcnow(),
        namespace="shared",
    )

    # Write fact directly using pipeline's _write_fact
    pipeline._write_fact(fact, entity, "acme corp", "literal", episode)

    # Query it back
    with neo4j_driver.session() as s:
        result = s.run(
            """
            MATCH (f:Fact)-[:SUBJECT]->(e:Entity {name: 'Alice'})
            RETURN f.id AS id, f.statement AS statement, f.predicate AS predicate,
                   f.epistemic_state AS epistemic_state
            """
        )
        record = result.single()
        assert record is not None
        assert record["predicate"] == "works_at"
        assert record["epistemic_state"] == "active"
        assert "alice" in record["statement"].lower()

    pipeline.close()


# --- Test 2: Entity canonicalization (alias lookup) ---


def test_entity_canonicalization(neo4j_driver):
    """Test that the same entity name resolves to the same entity."""
    # Create entity "Bob"
    entity1 = create_entity(neo4j_driver, "Bob", "entity", "First Bob")

    # Resolve "Bob" again — should hit alias, return same entity
    entity2 = resolve_entity(neo4j_driver, "Bob")

    assert entity1.id == entity2.id
    assert entity2.name == "Bob"

    # Resolve "BOB" — normalized, should still hit alias
    entity3 = resolve_entity(neo4j_driver, "BOB")
    assert entity1.id == entity3.id

    # Resolve "bob" — same
    entity4 = resolve_entity(neo4j_driver, "bob")
    assert entity1.id == entity4.id

    # Different name should create new entity
    entity5 = resolve_entity(neo4j_driver, "Charlie")
    assert entity5.id != entity1.id


def test_normalize_name():
    """Test name normalization."""
    assert normalize_name("Alice") == "alice"
    assert normalize_name("  Alice  ") == "alice"
    assert normalize_name("ALICE") == "alice"
    assert normalize_name("Al-ice") == "al ice"
    assert normalize_name("Al_ice") == "al ice"
    assert normalize_name("Al/ice") == "al ice"
    assert normalize_name("Alice!") == "alice"
    assert normalize_name("") == ""
    assert normalize_name("Alice Smith") == "alice smith"


# --- Test 3: LLM extraction via LiteLLM → Neo4j write ---


@pytest.mark.skipif(
    not os.environ.get("LITELLM_API_KEY"),
    reason="No LITELLM_API_KEY set",
)
def test_llm_extraction(neo4j_driver, redis_client):
    """Test LLM extraction through LiteLLM and verify output structure."""
    content = "Alice works at Acme Corp. She lives in New York. Bob is the CEO of Acme Corp."

    # Call extract
    extraction = extract(content, llm_client)

    # Verify extraction structure
    assert "entities" in extraction
    assert "facts" in extraction
    assert len(extraction["entities"]) >= 2  # Alice, Acme Corp, Bob, New York
    assert len(extraction["facts"]) >= 2  # works_at, lives_in, ceo_of

    # Verify all predicates are valid
    for fact in extraction["facts"]:
        assert is_valid_predicate(fact["predicate"]), f"Invalid predicate: {fact['predicate']}"

    # Verify entities have names
    for ent in extraction["entities"]:
        assert "name" in ent
        assert len(ent["name"]) > 0


@pytest.mark.skipif(
    not os.environ.get("LITELLM_API_KEY"),
    reason="No LITELLM_API_KEY set",
)
def test_full_pipeline_with_llm(neo4j_driver, redis_client):
    """Full pipeline: enqueue → process episode → query facts from Neo4j."""
    pipeline = IngestionPipeline(
        redis_url=REDIS_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
        llm_client=llm_client,
    )

    episode = Episode(
        content="Alice works at Acme Corp. Bob is the CEO of Acme Corp.",
        content_type="text",
        source={"test": "full_pipeline"},
    )

    # Enqueue and process
    pipeline.enqueue(episode)
    assert pipeline.get_queue_depth() == 1
    pipeline.process_next()
    assert pipeline.get_queue_depth() == 0

    # Query Neo4j for facts
    with neo4j_driver.session() as s:
        result = s.run(
            """
            MATCH (f:Fact)-[:SUBJECT]->(e:Entity)
            RETURN f.statement AS statement, f.predicate AS predicate,
                   e.name AS subject_name
            ORDER BY f.predicate
            """
        )
        facts = [dict(r) for r in result]

    assert len(facts) >= 2
    predicates = [f["predicate"] for f in facts]
    # Should have at least works_at and ceo_of (or similar)
    assert "works_at" in predicates or "employed_by" in predicates

    pipeline.close()


# --- Test 4: Temporal supersession on real data ---


def test_temporal_supersession(neo4j_driver):
    """Test that a functional predicate supersedes the old fact when value changes."""
    from ferrite.models import FactBase

    # Create entity
    entity = create_entity(neo4j_driver, "Dave", "entity", "Test entity")

    # Create episode
    episode = Episode(
        content="Dave works at Acme.",
        content_type="text",
        source={"test": "supersession"},
        recorded_at=datetime(2026, 1, 1),
    )

    # Create first fact: Dave works_at Acme
    fact1 = FactBase(
        predicate="works_at",
        statement="dave works_at acme",
        functional=True,
        valid_at=datetime(2026, 1, 1),
        recorded_at=datetime(2026, 1, 1),
        namespace="shared",
    )

    # Write first fact using _write_fact
    pipeline = IngestionPipeline(
        redis_url=REDIS_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    pipeline._write_fact(fact1, entity, "acme", "literal", episode)

    # Verify fact1 is active
    with neo4j_driver.session() as s:
        result = s.run(
            "MATCH (f:Fact {id: $id}) "
            "RETURN f.epistemic_state AS state, f.invalid_at AS invalid_at",
            id=fact1.id,
        )
        record = result.single()
        assert record["state"] == "active"
        assert record["invalid_at"] is None

    # Create second fact: Dave works_at Globex (new value for same functional predicate)
    episode2 = Episode(
        content="Dave now works at Globex.",
        content_type="text",
        source={"test": "supersession"},
        recorded_at=datetime(2026, 6, 1),
    )
    fact2 = FactBase(
        predicate="works_at",
        statement="dave works_at globex",
        functional=True,
        valid_at=datetime(2026, 6, 1),
        recorded_at=datetime(2026, 6, 1),
        namespace="shared",
    )

    # Detect supersession
    old_fact = detect_supersession(
        neo4j_driver, entity.id, "works_at", "globex", "literal", "shared"
    )
    assert old_fact is not None
    assert old_fact["id"] == fact1.id

    # Write new fact and apply supersession
    pipeline._write_fact(fact2, entity, "globex", "literal", episode2)
    apply_supersession(neo4j_driver, fact1.id, fact2.id, datetime(2026, 6, 1))

    # Verify fact1 is now superseded
    with neo4j_driver.session() as s:
        result = s.run(
            "MATCH (f:Fact {id: $id}) "
            "RETURN f.epistemic_state AS state, f.invalid_at AS invalid_at",
            id=fact1.id,
        )
        record = result.single()
        assert record["state"] == "superseded"
        assert record["invalid_at"] is not None

    # Verify fact2 is active
    with neo4j_driver.session() as s:
        result = s.run(
            "MATCH (f:Fact {id: $id}) RETURN f.epistemic_state AS state",
            id=fact2.id,
        )
        record = result.single()
        assert record["state"] == "active"

    # Verify SUPERSEDES edge
    with neo4j_driver.session() as s:
        result = s.run(
            """
            MATCH (new:Fact {id: $new_id})-[:SUPERSEDES]->(old:Fact {id: $old_id})
            RETURN count(*) AS count
            """,
            new_id=fact2.id,
            old_id=fact1.id,
        )
        record = result.single()
        assert record["count"] == 1

    pipeline.close()


# --- Test 5: Temporal queries (as-of-knowledge, as-of-world) ---


def test_temporal_queries(neo4j_driver):
    """Test as-of-knowledge and as-of-world temporal queries."""
    from ferrite.models import FactBase

    entity = create_entity(neo4j_driver, "Eve", "entity", "Test entity")

    # Create facts with different valid_at times
    # Fact 1: Eve's title was "engineer" from Jan 2025
    # Fact 2: Eve's title became "manager" from Jun 2025 (supersedes fact 1)
    episode = Episode(
        content="Eve's title history.",
        content_type="text",
        source={"test": "temporal_query"},
        recorded_at=datetime(2026, 1, 1),
    )

    fact1 = FactBase(
        predicate="has_title",
        statement="eve has_title engineer",
        functional=True,
        valid_at=datetime(2025, 1, 1),
        recorded_at=datetime(2026, 1, 1),
        namespace="shared",
    )

    fact2 = FactBase(
        predicate="has_title",
        statement="eve has_title manager",
        functional=True,
        valid_at=datetime(2025, 6, 1),
        recorded_at=datetime(2026, 1, 1),
        namespace="shared",
    )

    pipeline = IngestionPipeline(
        redis_url=REDIS_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    pipeline._write_fact(fact1, entity, "engineer", "literal", episode)
    pipeline._write_fact(fact2, entity, "manager", "literal", episode)
    apply_supersession(neo4j_driver, fact1.id, fact2.id, datetime(2025, 6, 1))

    # As-of-world: at March 2025, Eve's title should be "engineer" (fact1 valid, fact2 not yet)
    world_facts = get_history_as_of_world(
        neo4j_driver, entity.id, datetime(2025, 3, 1), "shared"
    )
    assert len(world_facts) == 1
    assert "engineer" in world_facts[0]["statement"]

    # As-of-world: at July 2025, Eve's title should be "manager" (fact2 valid, fact1 superseded)
    world_facts = get_history_as_of_world(
        neo4j_driver, entity.id, datetime(2025, 7, 1), "shared"
    )
    assert len(world_facts) == 1
    assert "manager" in world_facts[0]["statement"]

    # As-of-knowledge: at any time after recording, we know about both facts
    knowledge_facts = get_history_as_of_knowledge(
        neo4j_driver, entity.id, datetime(2026, 2, 1), "shared"
    )
    assert len(knowledge_facts) >= 2

    pipeline.close()


# --- Test 6: Semantic search via fulltext index ---


def test_fulltext_search(neo4j_driver):
    """Test Neo4j fulltext search on fact statements."""
    from ferrite.models import FactBase

    entity = create_entity(neo4j_driver, "Frank", "entity", "Test entity")
    episode = Episode(
        content="Frank works at Globex.",
        content_type="text",
        source={"test": "fulltext"},
    )

    fact = FactBase(
        predicate="works_at",
        statement="frank works at globex corporation as chief engineer",
        functional=True,
        valid_at=datetime.utcnow(),
        recorded_at=datetime.utcnow(),
        namespace="shared",
    )

    pipeline = IngestionPipeline(
        redis_url=REDIS_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    pipeline._write_fact(fact, entity, "globex corporation", "literal", episode)

    # Test fulltext search
    with neo4j_driver.session() as s:
        result = s.run(
            """
            CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $search_query)
            YIELD node, score
            RETURN node.statement AS statement, score
            ORDER BY score DESC
            LIMIT 5
            """,
            search_query="globex engineer",
        )
        records = [dict(r) for r in result]

    assert len(records) > 0
    assert "globex" in records[0]["statement"].lower()

    pipeline.close()


# --- Test 7: Contradiction detection ---


def test_contradiction_detection(neo4j_driver):
    """Test that contradictory facts are detected and flagged."""
    from ferrite.models import FactBase

    entity = create_entity(neo4j_driver, "Grace", "entity", "Test entity")
    episode = Episode(
        content="Grace works at Acme.",
        content_type="text",
        source={"test": "contradiction"},
    )

    fact1 = FactBase(
        predicate="works_at",
        statement="grace works_at acme",
        functional=True,
        valid_at=datetime.utcnow(),
        recorded_at=datetime.utcnow(),
        namespace="shared",
    )

    pipeline = IngestionPipeline(
        redis_url=REDIS_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    pipeline._write_fact(fact1, entity, "acme", "literal", episode)

    # Now try to detect contradiction for same object
    contradicted = detect_contradiction(
        neo4j_driver, entity.id, "works_at", "acme", True, "shared"
    )
    assert contradicted == fact1.id

    pipeline.close()
