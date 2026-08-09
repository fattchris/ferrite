"""Tests for entity normalization and alias resolution."""

import pytest

from ferrite.canonicalize import cosine_similarity, normalize_name


class TestNormalizeName:
    def test_lowercase(self):
        assert normalize_name("Acme Corp") == "acme corp"

    def test_strip_punctuation(self):
        assert normalize_name("Acme, Corp!") == "acme corp"

    def test_collapse_whitespace(self):
        assert normalize_name("  Acme   Corp  ") == "acme corp"

    def test_normalize_separators_hyphen(self):
        assert normalize_name("Acme-Corp") == "acme corp"

    def test_normalize_separators_slash(self):
        assert normalize_name("Acme/Corp") == "acme corp"

    def test_normalize_separators_underscore(self):
        assert normalize_name("Acme_Corp") == "acme corp"

    def test_strip_non_alphanumeric(self):
        assert normalize_name("Acme @ Corp #1") == "acme corp 1"

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_only_punctuation(self):
        assert normalize_name("!@#$%") == ""

    def test_numbers_preserved(self):
        assert normalize_name("GPT-4") == "gpt 4"

    def test_mixed_case_and_punctuation(self):
        assert normalize_name("  OpenAI, Inc.  ") == "openai inc"


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 0.5, 0.3]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_empty_vectors(self):
        assert cosine_similarity([], []) == 0.0

    def test_different_length_vectors(self):
        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_zero_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)


class TestNormalizeEdgeCases:
    def test_unicode_stripped(self):
        # Non-ASCII chars are stripped (only a-z0-9 and spaces kept)
        result = normalize_name("café")
        assert result == "caf"

    def test_multiple_separators(self):
        assert normalize_name("Acme- Corp_/Inc") == "acme corp inc"

    def test_tabs_and_newlines(self):
        assert normalize_name("Acme\tCorp\n") == "acme corp"
