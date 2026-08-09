"""Tests for entity normalization and alias resolution.""""

import pytest
from ferrite.canonicalize import normalize_name


def test_normalize_lowercase():
    assert normalize_name("Python") == "python"


def test_normalize_strip_punctuation():
    assert normalize_name("Python!!!") == "python"


def test_normalize_collapse_whitespace():
    assert normalize_name("  Python   Language  ") == "python language"


def test_normalize_separators():
    assert normalize_name("machine-learning") == "machine learning"
    assert normalize_name("machine_learning") == "machine learning"


def test_normalize_empty():
    assert normalize_name("") == ""
    assert normalize_name("   ") == ""


def test_normalize_mixed():
    assert normalize_name("  Open_AI  ") == "open ai"
    assert normalize_name("Node.js!!") == "node js"
