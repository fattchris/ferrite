"""Tests for supersession/contradiction logic.""""

import pytest
from ferrite.vocab import is_functional


def test_functional_predicate_lookup():
    assert is_functional("works_at") is True
    assert is_functional("version_is") is True
    assert is_functional("runs_on") is True


def test_nonfunctional_predicate_lookup():
    assert is_functional("uses") is False
    assert is_functional("depends_on") is False
    assert is_functional("related_to") is False


def test_nonexistent_predicate():
    assert is_functional("nonexistent_predicate") is False


def test_temporal_supersession_logic():
    # Verify functional predicates would trigger supersession
    assert is_functional("works_at")
    # Non-functional should not supersede
    assert not is_functional("uses")
