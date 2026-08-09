"""Tests for Fact/Entity model validation.""""

import pytest
from datetime import datetime
from ferrite.models import Fact, Entity, Episode, Certainty, EpistemicState, EntityType


def test_entity_defaults():
    e = Entity(name="Python")
    assert e.type == EntityType.entity
    assert e.summary is None
    assert len(e.id) > 0


def test_entity_concept_type():
    e = Entity(name="Machine Learning", type=EntityType.concept)
    assert e.type == EntityType.concept


def test_fact_defaults():
    f = Fact(predicate="works_at", statement="alice works_at acme")
    assert f.epistemic_state == EpistemicState.active
    assert f.certainty == Certainty.stated
    assert f.valid_at_inferred is True
    assert f.invalid_at is None


def test_fact_with_all_fields():
    now = datetime.now()
    f = Fact(
        predicate="version_is",
        statement="python version_is 3.11",
        functional=True,
        certainty=Certainty.inferred,
        epistemic_state=EpistemicState.superseded,
        valid_at=now,
        valid_at_inferred=False,
    )
    assert f.functional is True
    assert f.certainty == Certainty.inferred
    assert f.epistemic_state == EpistemicState.superseded
    assert f.valid_at == now


def test_episode_creation():
    ep = Episode(content="test content", source={"type": "test"})
    assert ep.content == "test content"
    assert ep.source["type"] == "test"
    assert len(ep.id) > 0
