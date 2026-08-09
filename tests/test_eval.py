"""Tests for the eval harness (§13.2, A5)."""

from pathlib import Path
from unittest.mock import MagicMock

from ferrite.eval import (
    _compute_mrr,
    _compute_recall_at_k,
    _compute_substring_match,
    health_check,
    load_queries,
    run_eval,
)


class TestRecallAtK:
    def test_perfect_recall(self):
        results = [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]
        expected = ["f1", "f2"]
        assert _compute_recall_at_k(results, expected, 5) == 1.0

    def test_partial_recall(self):
        results = [{"id": "f1"}, {"id": "f4"}, {"id": "f3"}]
        expected = ["f1", "f2"]
        assert _compute_recall_at_k(results, expected, 5) == 0.5

    def test_no_results(self):
        assert _compute_recall_at_k([], ["f1"], 5) == 0.0

    def test_no_expectations(self):
        assert _compute_recall_at_k([], [], 5) == 1.0

    def test_k_limit(self):
        results = [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]
        expected = ["f3"]
        # f3 is at rank 3, not in top-2
        assert _compute_recall_at_k(results, expected, 2) == 0.0
        assert _compute_recall_at_k(results, expected, 3) == 1.0


class TestMRR:
    def test_first_result_relevant(self):
        results = [{"id": "f1"}, {"id": "f2"}]
        assert _compute_mrr(results, ["f1"]) == 1.0

    def test_second_result_relevant(self):
        results = [{"id": "f1"}, {"id": "f2"}]
        assert _compute_mrr(results, ["f2"]) == 0.5

    def test_no_relevant(self):
        results = [{"id": "f1"}]
        assert _compute_mrr(results, ["f2"]) == 0.0


class TestSubstringMatch:
    def test_match_found(self):
        results = [{"statement": "spark-01 runs glm-5.2"}]
        assert _compute_substring_match(results, ["glm-5.2"]) is True

    def test_no_match(self):
        results = [{"statement": "unrelated fact"}]
        assert _compute_substring_match(results, ["glm-5.2"]) is False

    def test_no_substrings(self):
        assert _compute_substring_match([], []) is True


class TestLoadQueries:
    def test_loads_from_yaml(self, tmp_path):
        qfile = tmp_path / "queries.yaml"
        qfile.write_text(
            "queries:\n"
            "  - text: 'test query'\n"
            "    class: entity_lookup\n"
            "    expected_entity_ids: ['spark-01']\n"
        )
        queries = load_queries(qfile)
        assert len(queries) == 1
        assert queries[0]["text"] == "test query"

    def test_missing_file(self):
        queries = load_queries(Path("/nonexistent/path.yaml"))
        assert queries == []


class TestHealthCheck:
    def test_valid_queries_file(self, tmp_path):
        qfile = tmp_path / "queries.yaml"
        qfile.write_text(
            "queries:\n"
            "  - text: 'test'\n"
            "    class: entity_lookup\n"
        )
        result = health_check(qfile)
        assert result["status"] == "ok"
        assert result["queries"] == 1

    def test_missing_file(self):
        result = health_check(Path("/nonexistent/path.yaml"))
        assert result["status"] == "warning"

    def test_malformed_query(self, tmp_path):
        qfile = tmp_path / "queries.yaml"
        qfile.write_text(
            "queries:\n"
            "  - class: entity_lookup\n"  # missing 'text'
        )
        result = health_check(qfile)
        assert result["status"] == "error"


class TestRunEval:
    def test_run_with_no_queries(self):
        mock_driver = MagicMock()
        result = run_eval(
            mock_driver,
            queries_file=Path("/nonexistent/path.yaml"),
        )
        assert "error" in result

    def test_run_with_queries(self, tmp_path):
        qfile = tmp_path / "queries.yaml"
        qfile.write_text(
            "queries:\n"
            "  - text: 'spark-01'\n"
            "    class: entity_lookup\n"
            "    expected_entity_ids: ['f1']\n"
        )
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(
            return_value=mock_session
        )
        mock_driver.session.return_value.__exit__ = MagicMock(
            return_value=None
        )
        mock_session.run.return_value = iter([])

        result = run_eval(mock_driver, queries_file=qfile)
        assert result["total_queries"] == 1
        assert "recall" in result
        assert "mrr" in result
