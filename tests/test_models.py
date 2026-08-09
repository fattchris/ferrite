"""Tests for Fact/Entity Pydantic model validation."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from ferrite.models import Alias, Entity, Episode, FactBase, Observation


class TestFactBase:
    def test_fact_creation_with_defaults(self):
        fact = FactBase(
            predicate="works_at",
            statement="alice works_at acme",
            functional=True,
            valid_at=datetime(2024, 1, 1),
        )
        assert fact.predicate == "works_at"
        assert fact.statement == "alice works_at acme"
        assert fact.functional is True
        assert fact.certainty == "stated"
        assert fact.epistemic_state == "active"
        assert fact.assertion_source == "user"
        assert fact.valid_at_inferred is True
        assert fact.invalid_at is None
        assert fact.namespace == "shared"
        assert fact.id is not None

    def test_fact_with_all_fields(self):
        fact = FactBase(
            predicate="runs_on",
            statement="app runs_on linux",
            functional=True,
            certainty="inferred",
            epistemic_state="superseded",
            assertion_source="model",
            valid_at=datetime(2024, 3, 15),
            valid_at_inferred=False,
            invalid_at=datetime(2024, 6, 1),
            recorded_at=datetime(2024, 6, 1),
            namespace="personal",
        )
        assert fact.certainty == "inferred"
        assert fact.epistemic_state == "superseded"
        assert fact.assertion_source == "model"
        assert fact.valid_at_inferred is False
        assert fact.invalid_at == datetime(2024, 6, 1)
        assert fact.namespace == "personal"

    def test_fact_invalid_certainty(self):
        with pytest.raises(ValidationError):
            FactBase(
                predicate="works_at",
                statement="test",
                functional=True,
                certainty="maybe",
                valid_at=datetime(2024, 1, 1),
            )

    def test_fact_invalid_epistemic_state(self):
        with pytest.raises(ValidationError):
            FactBase(
                predicate="works_at",
                statement="test",
                functional=True,
                epistemic_state="deleted",
                valid_at=datetime(2024, 1, 1),
            )

    def test_fact_invalid_assertion_source(self):
        with pytest.raises(ValidationError):
            FactBase(
                predicate="works_at",
                statement="test",
                functional=True,
                assertion_source="system",
                valid_at=datetime(2024, 1, 1),
            )

    def test_fact_invalid_namespace(self):
        with pytest.raises(ValidationError):
            FactBase(
                predicate="works_at",
                statement="test",
                functional=True,
                namespace="public",
                valid_at=datetime(2024, 1, 1),
            )

    def test_fact_auto_generates_id(self):
        f1 = FactBase(
            predicate="works_at",
            statement="test",
            functional=True,
            valid_at=datetime(2024, 1, 1),
        )
        f2 = FactBase(
            predicate="works_at",
            statement="test",
            functional=True,
            valid_at=datetime(2024, 1, 1),
        )
        assert f1.id != f2.id


class TestEntity:
    def test_entity_creation(self):
        entity = Entity(name="Acme Corp", type="entity", summary="A company")
        assert entity.name == "Acme Corp"
        assert entity.type == "entity"
        assert entity.summary == "A company"
        assert entity.id is not None

    def test_entity_default_type(self):
        entity = Entity(name="Test")
        assert entity.type == "entity"

    def test_entity_invalid_type(self):
        with pytest.raises(ValidationError):
            Entity(name="Test", type="person")


class TestEpisode:
    def test_episode_creation(self):
        ep = Episode(
            content="Alice works at Acme Corp",
            content_type="text",
            source={"url": "http://example.com"},
        )
        assert ep.content == "Alice works at Acme Corp"
        assert ep.content_type == "text"
        assert ep.source == {"url": "http://example.com"}
        assert ep.namespace == "shared"
        assert ep.id is not None

    def test_episode_with_namespace(self):
        ep = Episode(
            content="My personal note",
            content_type="text",
            source={},
            namespace="personal",
        )
        assert ep.namespace == "personal"


class TestObservation:
    def test_observation_creation(self):
        obs = Observation(episode_id="ep-123", fact_id="fact-456")
        assert obs.episode_id == "ep-123"
        assert obs.fact_id == "fact-456"
        assert obs.id is not None


class TestAlias:
    def test_alias_creation(self):
        alias = Alias(norm="acme corp")
        assert alias.norm == "acme corp"
