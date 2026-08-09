"""Tests for the OllamaEmbedder and cosine similarity."""

from unittest.mock import MagicMock, patch

from ferrite.embeddings import EMBEDDING_DIM, OllamaEmbedder, cosine_similarity


class TestOllamaEmbedder:
    def test_embed_success(self):
        """Test successful embedding returns 768d vector."""
        import json as _json

        embedder = OllamaEmbedder()
        mock_response = MagicMock()
        mock_response.read.return_value = _json.dumps(
            {"embedding": [0.1] * EMBEDDING_DIM}
        ).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)

        with patch("ferrite.embeddings.urllib.request.urlopen", return_value=mock_response):
            result = embedder.embed("test text")
            assert result is not None
            assert len(result) == EMBEDDING_DIM

    def test_embed_empty_text(self):
        """Empty text returns None."""
        embedder = OllamaEmbedder()
        assert embedder.embed("") is None
        assert embedder.embed("   ") is None

    def test_embed_ollama_unreachable(self):
        """When Ollama is unreachable, embed returns None (graceful degradation)."""
        embedder = OllamaEmbedder(host="http://localhost:99999")
        result = embedder.embed("test")
        assert result is None

    def test_embed_batch(self):
        """Batch embedding returns list of embeddings."""
        embedder = OllamaEmbedder()
        mock_embedding = [0.1] * EMBEDDING_DIM

        with patch.object(embedder, "embed", return_value=mock_embedding):
            results = embedder.embed_batch(["text1", "text2", "text3"])
            assert len(results) == 3
            assert all(r == mock_embedding for r in results)

    def test_is_available(self):
        """is_available returns True when Ollama is reachable."""
        embedder = OllamaEmbedder(host="http://localhost:99999")
        assert embedder.is_available() is False


class TestCosineSimilarity:
    def test_identical_vectors(self):
        """Identical vectors have cosine similarity 1.0."""
        v = [0.1, 0.2, 0.3, 0.4]
        sim = cosine_similarity(v, v)
        assert abs(sim - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        """Orthogonal vectors have cosine similarity 0.0."""
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        sim = cosine_similarity(a, b)
        assert abs(sim) < 1e-6

    def test_zero_vector(self):
        """Zero vector returns 0.0 similarity."""
        sim = cosine_similarity([0.0, 0.0], [1.0, 2.0])
        assert sim == 0.0
