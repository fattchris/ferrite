"""Tests for the LLM extraction module."""
import json
import pytest
from ferrite.extractor import extract, parse_extraction_response, _extract_json_from_response


class TestParseExtractionResponse:
    def test_valid_json(self):
        response = json.dumps({
            "entities": [{"name": "Chris", "type": "person"}],
            "facts": [{"subject": "Chris", "predicate": "works_at", "object": "Stoke", "object_type": "entity"}]
        })
        result = parse_extraction_response(response)
        assert len(result.entities) == 1
        assert len(result.facts) == 1

    def test_markdown_fenced_json(self):
        response = '```json\n{"entities": [], "facts": []}\n```'
        result = parse_extraction_response(response)
        assert result.entities == []
        assert result.facts == []

    def test_json_embedded_in_prose(self):
        response = 'Here are the results:\n{"entities": [{"name": "GLM"}], "facts": []}\nDone.'
        result = parse_extraction_response(response)
        assert len(result.entities) == 1

    def test_missing_entities_key(self):
        response = json.dumps({"facts": []})
        result = parse_extraction_response(response)
        assert result.entities == []

    def test_missing_facts_key(self):
        response = json.dumps({"entities": []})
        result = parse_extraction_response(response)
        assert result.facts == []

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            parse_extraction_response("not json at all")


class TestExtract:
    def test_none_llm_client_returns_empty(self):
        result = extract("some content", llm_client=None)
        assert len(result.entities) == 0
        assert len(result.facts) == 0

    def test_llm_returns_none_returns_empty(self):
        """LLM client returns None instead of a string — should not crash."""
        def mock_llm(system, user):
            return None
        result = extract("some content", llm_client=mock_llm)
        assert len(result.entities) == 0
        assert len(result.facts) == 0

    def test_llm_returns_empty_string_returns_empty(self):
        """LLM client returns empty string — should not crash."""
        def mock_llm(system, user):
            return ""
        result = extract("some content", llm_client=mock_llm)
        assert len(result.entities) == 0
        assert len(result.facts) == 0

    def test_llm_returns_garbage_returns_empty(self):
        """LLM returns non-JSON text — should return empty, not crash."""
        def mock_llm(system, user):
            return "I cannot extract facts from this."
        result = extract("some content", llm_client=mock_llm)
        assert len(result.entities) == 0
        assert len(result.facts) == 0

    def test_llm_returns_valid_json(self):
        def mock_llm(system, user):
            return json.dumps({
                "entities": [{"name": "Spark-01", "type": "server"}],
                "facts": [{
                    "subject": "Spark-01",
                    "predicate": "runs_model",
                    "object": "GLM-5.2",
                    "object_type": "entity"
                }]
            })
        result = extract("Spark-01 runs GLM-5.2", llm_client=mock_llm)
        assert len(result.entities) == 1
        assert len(result.facts) == 1
        assert result.facts[0].predicate == "runs_model"

    def test_llm_returns_partial_json_returns_empty(self):
        """LLM returns truncated JSON — should not crash."""
        def mock_llm(system, user):
            return '{"entities": [{"name": "Spark'
        result = extract("some content", llm_client=mock_llm)
        assert len(result.entities) == 0
        assert len(result.facts) == 0
