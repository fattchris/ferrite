"""Tests for TEMPR multi-strategy retrieval and RRF fusion."""

from unittest.mock import MagicMock

from ferrite.tempr import (
    _parse_time_expression,
    _rerank_by_epistemic_state,
    _rrf_fuse,
    tempr_search,
)


class TestRRFFusion:
    def test_single_list(self):
        """RRF with one list returns items in order."""
        items = [
            {"id": "f1", "statement": "a", "score": 1.0},
            {"id": "f2", "statement": "b", "score": 0.8},
        ]
        result = _rrf_fuse([items])
        assert result[0]["id"] == "f1"
        assert result[1]["id"] == "f2"

    def test_two_lists_overlap(self):
        """Items in both lists get higher RRF scores."""
        list_a = [
            {"id": "f1", "statement": "a"},
            {"id": "f2", "statement": "b"},
            {"id": "f3", "statement": "c"},
        ]
        list_b = [
            {"id": "f3", "statement": "c"},
            {"id": "f1", "statement": "a"},
            {"id": "f4", "statement": "d"},
        ]
        result = _rrf_fuse([list_a, list_b])
        # f1 and f3 appear in both → higher scores
        ids = [r["id"] for r in result]
        assert "f1" in ids[:2]
        assert "f3" in ids[:2]

    def test_empty_lists(self):
        """Empty input returns empty."""
        assert _rrf_fuse([]) == []
        assert _rrf_fuse([[]]) == []

    def test_dedup_by_id(self):
        """Same ID in multiple lists appears once."""
        list_a = [{"id": "f1", "statement": "a"}]
        list_b = [{"id": "f1", "statement": "a"}]
        result = _rrf_fuse([list_a, list_b])
        assert len(result) == 1


class TestEpistemicRerank:
    def test_active_before_contradicted(self):
        """Active facts ranked before contradicted."""
        results = [
            {"id": "f1", "epistemic_state": "contradicted", "score": 0.5},
            {"id": "f2", "epistemic_state": "active", "score": 0.3},
        ]
        reranked = _rerank_by_epistemic_state(results)
        assert reranked[0]["id"] == "f2"  # active first

    def test_superseded_excluded(self):
        """Superseded facts are excluded by default."""
        results = [
            {"id": "f1", "epistemic_state": "active", "score": 0.5},
            {"id": "f2", "epistemic_state": "superseded", "score": 0.9},
        ]
        reranked = _rerank_by_epistemic_state(results)
        assert len(reranked) == 1
        assert reranked[0]["id"] == "f1"


class TestTimeParsing:
    def test_last_spring(self):
        start, end = _parse_time_expression("what happened last spring?")
        assert start.month == 3
        assert end.month == 6

    def test_in_june(self):
        start, end = _parse_time_expression("events in june")
        assert start.month == 6
        assert start.day == 1

    def test_explicit_date(self):
        start, end = _parse_time_expression("2026-01-15 deployment")
        assert start.year == 2026
        assert start.month == 1
        assert start.day == 15

    def test_year_month(self):
        start, end = _parse_time_expression("2026-03 changes")
        assert start.year == 2026
        assert start.month == 3

    def test_no_time_expression(self):
        assert _parse_time_expression("what is spark-01?") is None

    def test_last_week(self):
        start, end = _parse_time_expression("what changed last week")
        assert start is not None
        assert end is not None

    def test_last_month(self):
        start, end = _parse_time_expression("last month summary")
        assert start is not None


class TestTemprSearch:
    def test_all_strategies_fail_returns_empty(self):
        """If all strategies return nothing, return empty list."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)
        mock_session.run.return_value = iter([])

        result = tempr_search(mock_driver, "nonexistent query")
        assert result == []

    def test_bm25_only(self):
        """When only BM25 returns results, those are returned."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        bm25_results = [
            {"id": "f1", "statement": "test", "predicate": "p",
             "epistemic_state": "active", "score": 1.0},
        ]
        mock_session.run.return_value = iter(bm25_results)

        result = tempr_search(mock_driver, "test")
        assert len(result) >= 1
