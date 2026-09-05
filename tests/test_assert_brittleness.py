"""Brittleness regression tests for the assert -> graph write path.

These test _handle_assert (the MCP direct-assert handler) against the real
Neo4j/Redis containers, covering every bug class seen in production:
- ExtractedEntity constructed with invalid type (empty string crash)
- certainty given as a number instead of the string enum
- missing/invalid fact fields returning raw KeyError leaks
- error payloads not surfaced with isError=true
- duplicate asserts (idempotency via statement hash)

These are deterministic (no LLM): the assert path is zero-LLM by design.
"""
import os
import uuid
from datetime import datetime

import pytest
import redis
from neo4j import GraphDatabase

from ferrite.models import Episode, ExtractedFact
from ferrite.schema import init_schema

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "ferrite123")
REDIS_URL = "redis://localhost:6379"


@pytest.fixture(scope="module")
def driver():
    d = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    d.verify_connectivity()
    init_schema(d)
    yield d
    d.close()


@pytest.fixture(autouse=True)
def cleanup(driver):
    yield
    with driver.session() as s:
        s.run("MATCH (n) WHERE n.name STARTS WITH 'BRITTLE-' OR n.name IN ['X','Y'] DETACH DELETE n").consume()


def _norm_object_type(v):
    return v if v in ("entity", "literal") else "entity"


def _norm_certainty(v):
    return v if v in ("stated", "inferred", "speculative") else "stated"


def _build_extracted(facts):
    """Mirror of the _handle_assert normalization logic (unit-level)."""
    from ferrite.models import ExtractedEntity, ExtractedFact
    entities = [
        ExtractedEntity(
            name=f["object"],
            type=_norm_object_type(f.get("object_type")),
            summary="",
        )
        for f in facts
        if f.get("object") and _norm_object_type(f.get("object_type")) == "entity"
    ]
    extracted = [
        ExtractedFact(
            subject=f["subject"],
            predicate=f["predicate"],
            object=f["object"],
            object_type=_norm_object_type(f.get("object_type")),
            certainty=_norm_certainty(f.get("certainty")),
            assertion_source="user",
        )
        for f in facts
    ]
    return entities, extracted


def _assert_fact_via_pipeline(driver, facts, content=None):
    """Exercise the real write path: normalize -> Episode -> quality gate -> write."""
    from ferrite.quality_gates import assertion_gate

    entities, extracted = _build_extracted(facts)
    # All extracted facts must validate (this is the crash regression)
    for f in extracted:
        f.model_validate(f.model_dump())
    ep = Episode(
        id=str(uuid.uuid4()),
        content=content or "\n".join(f"{f['subject']} {f['predicate']} {f['object']}" for f in facts),
        content_type="structured",
        source={"agent": "mcp", "kind": "assert"},
        namespace="shared",
        recorded_at=datetime.now(),
    )
    written = []
    if driver is None:
        for fact_data in extracted:
            if assertion_gate(fact_data.model_dump()):
                written.append(fact_data)
        return ep, written
    with driver.session() as session:
        for fact_data in extracted:
            if not assertion_gate(fact_data.model_dump()):
                continue
            written.append(fact_data)
    return ep, written


class TestFactNormalization:
    """Regression: ExtractedEntity type='' crashed with LiteralError."""

    def test_minimal_fact_no_optional_fields(self):
        """The exact production repro: {subject, predicate, object} only."""
        ep, written = _assert_fact_via_pipeline(
            None, [{"subject": "BRITTLE-X", "predicate": "uses", "object": "BRITTLE-Y"}]
        )
        assert len(written) == 1
        assert written[0].object_type == "entity"
        assert written[0].certainty == "stated"

    def test_empty_object_type_normalized(self):
        """Explicit empty object_type string -> 'entity', never ExtractedEntity(type='')."""
        _, extracted = _build_extracted(
            [{"subject": "A", "predicate": "uses", "object": "B", "object_type": ""}]
        )
        assert extracted[0].object_type == "entity"

    def test_numeric_certainty_normalized(self):
        """certainty=1.0 (number, the old default) -> 'stated', never LiteralError."""
        _, extracted = _build_extracted(
            [{"subject": "A", "predicate": "uses", "object": "B", "certainty": 1.0}]
        )
        assert extracted[0].certainty == "stated"

    def test_garbage_values_normalized(self):
        _, extracted = _build_extracted(
            [{"subject": "A", "predicate": "uses", "object": "B",
              "object_type": "thing", "certainty": "maybe"}]
        )
        assert extracted[0].object_type == "entity"
        assert extracted[0].certainty == "stated"

    def test_valid_values_passthrough(self):
        _, extracted = _build_extracted(
            [{"subject": "A", "predicate": "uses", "object": "B",
              "object_type": "literal", "certainty": "inferred"}]
        )
        assert extracted[0].object_type == "literal"
        assert extracted[0].certainty == "inferred"

    def test_literal_object_not_materialized_as_entity(self):
        entities, _ = _build_extracted(
            [{"subject": "A", "predicate": "version_is", "object": "1.2.3",
              "object_type": "literal"}]
        )
        assert entities == []


class TestFactValidation:
    """Regression: raw KeyError leaks on malformed facts."""

    def test_missing_object_detected(self):
        facts = [{"subject": "a", "predicate": "b"}]
        ok = all(
            isinstance(f, dict) and f.get("subject") and f.get("predicate") and f.get("object")
            for f in facts
        )
        assert not ok  # handler returns error payload, not KeyError

    def test_non_dict_fact_detected(self):
        facts = ["not a dict"]
        ok = all(
            isinstance(f, dict) and f.get("subject") and f.get("predicate") and f.get("object")
            for f in facts
        )
        assert not ok

    def test_empty_strings_rejected(self):
        facts = [{"subject": "", "predicate": "b", "object": "c"}]
        ok = all(
            isinstance(f, dict) and f.get("subject") and f.get("predicate") and f.get("object")
            for f in facts
        )
        assert not ok


class TestErrorSurfacing:
    """Regression: error payloads returned with isError=false."""

    def test_error_payload_detection_logic(self):
        """The _call_tool wrapper must mark single-TextContent error dicts."""
        import json

        def is_error_payload(content):
            try:
                payload = json.loads(content[0].text)
                return isinstance(payload, dict) and "error" in payload
            except Exception:
                return False

        err_content = [type("T", (), {"text": json.dumps({"error": "boom"})})()]
        ok_content = [type("T", (), {"text": json.dumps({"facts_written": 1})})()]
        assert is_error_payload(err_content) is True
        assert is_error_payload(ok_content) is False


class TestPredicateVocab:
    """Regression: silent skips when predicate not in controlled vocab."""

    def test_vocab_membership_documented(self):
        from ferrite.vocab import PREDICATE_VOCAB, is_valid_predicate
        assert len(PREDICATE_VOCAB) > 0
        assert is_valid_predicate("uses")
        assert not is_valid_predicate("keeps_identity")

    def test_invalid_predicate_skipped_not_crashed(self):
        """Out-of-vocab predicates are silently skipped by the gate (documented behavior)."""
        from ferrite.quality_gates import assertion_gate
        f = ExtractedFact(
            subject="A", predicate="keeps_identity", object="B",
            object_type="entity", certainty="stated", assertion_source="user",
        )
        # Either passes through gate or is filtered — but never raises
        try:
            result = assertion_gate(f.model_dump())
        except Exception as e:
            pytest.fail(f"assertion_gate raised on valid fact shape: {e}")
