"""Tests for vector search and RRF hybrid fusion."""

from unittest.mock import MagicMock

from ferrite.query import hybrid_search, search_facts, vector_search


class TestSearchFactsFallback:
    """Test that search_facts falls back to BM25 when no embedder."""

    def test_no_embedder_uses_bm25(self):
        """Without embedder, search_facts uses BM25 only."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_result = iter([
            {"id": "f1", "statement": "test fact", "predicate": "runs_on",
             "score": 1.0}
        ])
        mock_session.run.return_value = mock_result

        results = search_facts(mock_driver, "test query", embedder=None)
        assert len(results) >= 1

    def test_with_unavailable_embedder_falls_back(self):
        """When embedder is set but Ollama is unreachable, falls back to BM25."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = None  # Ollama unreachable

        mock_session.run.return_value = iter([
            {"id": "f1", "statement": "test", "predicate": "test", "score": 1.0}
        ])

        results = search_facts(mock_driver, "test", embedder=mock_embedder)
        # Should fall back to BM25
        assert isinstance(results, list)


class TestHybridSearch:
    """Test RRF fusion logic."""

    def test_rrf_fusion(self):
        """Test that RRF combines BM25 and vector results."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768

        # BM25 returns f1, f2, f3
        bm25_results = [
            {"id": "f1", "statement": "fact 1", "predicate": "p1", "score": 2.0},
            {"id": "f2", "statement": "fact 2", "predicate": "p2", "score": 1.5},
            {"id": "f3", "statement": "fact 3", "predicate": "p3", "score": 1.0},
        ]

        # Vector returns f3, f4, f1
        vec_results = [
            {"id": "f3", "statement": "fact 3", "predicate": "p3", "score": 0.9},
            {"id": "f4", "statement": "fact 4", "predicate": "p4", "score": 0.8},
            {"id": "f1", "statement": "fact 1", "predicate": "p1", "score": 0.7},
        ]

        # First call = BM25, second = vector
        mock_session.run.side_effect = [
            iter(bm25_results),
            iter(vec_results),
        ]

        results = hybrid_search(mock_driver, "test", mock_embedder, limit=5)

        # f1 appears in both at rank 0 and rank 2 → highest RRF score
        assert len(results) > 0
        # f1 should rank first (appears in both lists)
        assert results[0]["id"] == "f1"

    def test_no_vector_results_returns_bm25(self):
        """When vector search returns nothing, return BM25 only."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = None  # Ollama unreachable

        bm25_results = [
            {"id": "f1", "statement": "fact 1", "predicate": "p1", "score": 1.0},
        ]

        mock_session.run.return_value = iter(bm25_results)

        results = hybrid_search(mock_driver, "test", mock_embedder, limit=5)
        assert len(results) >= 1
        assert results[0]["id"] == "f1"


class TestVectorSearch:
    def test_vector_search_no_embedding(self):
        """When embedder returns None, vector_search returns empty list."""
        mock_driver = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = None

        results = vector_search(mock_driver, "test", mock_embedder)
        assert results == []

    def test_vector_search_exception_returns_empty(self):
        """When Neo4j throws, vector_search returns empty list (graceful)."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768

        mock_session.run.side_effect = Exception("Vector index not found")

        results = vector_search(mock_driver, "test", mock_embedder)
        assert results == []
