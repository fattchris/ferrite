"""Embedding generation via Ollama (nomic-embed-text-v1.5, 768d).

Per spec §7.3:
- Model: nomic-embed-text-v1.5 (768 dimensions, local via Ollama)
- Context window: 8192 tokens
- Fact statements are embedded at write time (F-2)
- Graceful degradation: if Ollama unreachable, return None (BM25-only)
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Optional

from .retry import retry

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_HOST = "http://localhost:11434"
EMBEDDING_DIM = 768


class OllamaEmbedder:
    """Generate embeddings via Ollama's /api/embeddings endpoint."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        host: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        if model_name is None or host is None:
            from .config import get_settings
            _settings = get_settings()
            if model_name is None:
                model_name = _settings.EMBED_MODEL
            if host is None:
                # OllamaEmbedder expects the Ollama base URL (without /v1 suffix)
                host = _settings.EMBED_BASE_URL.rstrip("/v1").rstrip("/")
        self.model_name = model_name
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._available: Optional[bool] = None

    @retry(max_attempts=3, backoff_base=0.5)
    def embed(self, text: str) -> Optional[list[float]]:
        """Embed a single text. Returns None if Ollama is unreachable."""
        if not text or not text.strip():
            return None
        data = json.dumps({"model": self.model_name, "prompt": text}).encode()
        req = urllib.request.Request(
            f"{self.host}/api/embeddings",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                result = json.loads(r.read())
                embedding = result.get("embedding")
                if embedding and len(embedding) == EMBEDDING_DIM:
                    self._available = True
                    return embedding
                emb_len = len(embedding) if embedding else "None"
                logger.warning("Unexpected embedding dim: %s", emb_len)
                return None
        except Exception as e:
            if self._available is not False:
                logger.warning("Ollama embedding failed (degrading to BM25-only): %s", e)
            self._available = False
            return None

    def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """Embed multiple texts. Returns list of embeddings (or None per text on failure)."""
        return [self.embed(t) for t in texts]

    def is_available(self) -> bool:
        """Check if Ollama is reachable and the model is loaded."""
        if self._available is not None:
            return self._available
        result = self.embed("health check")
        self._available = result is not None
        return self._available


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
