"""Tests for controlled predicate vocabulary and functional lookup."""

import pytest

from ferrite.vocab import (
    PREDICATE_VOCAB,
    get_predicate,
    is_functional,
    is_valid_predicate,
    list_predicates,
)


class TestVocabulary:
    def test_vocab_has_entries(self):
        assert len(PREDICATE_VOCAB) >= 30

    def test_functional_predicates(self):
        assert is_functional("works_at") is True
        assert is_functional("version_is") is True
        assert is_functional("runs_on") is True
        assert is_functional("born_on") is True

    def test_non_functional_predicates(self):
        assert is_functional("uses") is False
        assert is_functional("depends_on") is False
        assert is_functional("related_to") is False
        assert is_functional("author_of") is False

    def test_get_predicate_returns_metadata(self):
        meta = get_predicate("works_at")
        assert meta["functional"] is True
        assert "description" in meta

    def test_unknown_predicate_raises(self):
        with pytest.raises(ValueError, match="Unknown predicate"):
            get_predicate("nonexistent_predicate")

    def test_is_functional_unknown_raises(self):
        with pytest.raises(ValueError):
            is_functional("nonexistent_predicate")

    def test_is_valid_predicate(self):
        assert is_valid_predicate("works_at") is True
        assert is_valid_predicate("uses") is True
        assert is_valid_predicate("nonexistent") is False

    def test_list_predicates(self):
        preds = list_predicates()
        assert "works_at" in preds
        assert "related_to" in preds
        assert len(preds) >= 30

    def test_all_entries_have_functional_flag(self):
        for pred_id, meta in PREDICATE_VOCAB.items():
            assert "functional" in meta, f"{pred_id} missing functional flag"
            assert isinstance(meta["functional"], bool)

    def test_related_to_is_non_functional(self):
        """Spec: RELATED_TO edge type is deleted; relationships are Facts
        with 'related_to' predicate, non-functional."""
        assert "related_to" in PREDICATE_VOCAB
        assert is_functional("related_to") is False
