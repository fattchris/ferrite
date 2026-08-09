"""Tests for controlled predicate vocabulary.""""

import pytest
from ferrite.vocab import get_predicate, is_functional, is_valid_predicate, VOCAB


def test_vocab_has_entries():
    assert len(VOCAB) >= 30


def test_get_predicate_functional():
    p = get_predicate("works_at")
    assert p is not None
    assert p["functional"] is True


def test_get_predicate_non_functional():
    p = get_predicate("uses")
    assert p is not None
    assert p["functional"] is False


def test_get_predicate_nonexistent():
    assert get_predicate("nonexistent") is None


def test_is_valid_predicate():
    assert is_valid_predicate("works_at") is True
    assert is_valid_predicate("uses") is True
    assert is_valid_predicate("invalid_pred") is False


def test_related_to_exists_and_non_functional():
    assert is_valid_predicate("related_to")
    assert is_functional("related_to") is False


def test_all_functional_predicates():
    for pred_id, info in VOCAB.items():
        if info["functional"]:
            assert is_functional(pred_id) is True
        else:
            assert is_functional(pred_id) is False
