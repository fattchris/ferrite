"""Pluggable vector store — ABC + Neo4j implementation.

Per spec §7.4:
- MVP: Neo4j built-in vector index (768d, cosine similarity)
- Interface: VectorStore ABC — both ingestion (writes) and search (reads) use it
- Migration trigger: At ~500K embeddings, swap to Qdrant (config change only)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)


class VectorStore(ABC):
    """Abstract vector store interface."""

    @abstractmethod
    def store(self, node_id: str, embedding: list[float], metadata: Optional[dict] = None) -> bool:
        """Store an embedding for a node. Returns True on success."""

    @abstractmethod
    def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: Optional[dict] = None,
    ) -> list[dict[str, Any]]:
        """Search for similar vectors. Returns list of {id, score, metadata}."""

    @abstractmethod
    def count(self) -> int:
        """Count total stored embeddings."""


class Neo4jVectorStore(VectorStore):
    """Neo4j vector index implementation (768d cosine)."""

    VECTOR_INDEX_NAME = "fact_embeddings"
    VECTOR_DIMENSIONS = 768

    def __init__(self, driver) -> None:
        self.driver = driver

    def create_vector_index(self) -> None:
        """Create the vector index if it doesn't exist."""
        cypher = (
            f"CREATE VECTOR INDEX {self.VECTOR_INDEX_NAME} IF NOT EXISTS "
            f"FOR (f:Fact) ON (f.embedding) "
            f"OPTIONS {{indexConfig: {{"
            f"`vector.dimensions`: {self.VECTOR_DIMENSIONS}, "
            f"`vector.similarity_function`: 'cosine'"
            f"}}}}"
        )
        with self.driver.session() as s:
            s.run(cypher)
        logger.info("Vector index '%s' ensured", self.VECTOR_INDEX_NAME)

    def store(self, node_id: str, embedding: list[float], metadata: Optional[dict] = None) -> bool:
        """Write embedding to a Fact node."""
        if embedding is None or len(embedding) != self.VECTOR_DIMENSIONS:
            logger.warning("Skipping store: embedding is None or wrong dim")
            return False
        cypher = "MATCH (f:Fact {id: $fact_id}) SET f.embedding = $embedding"
        with self.driver.session() as s:
            s.run(cypher, fact_id=node_id, embedding=embedding)
        return True

    def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: Optional[dict] = None,
    ) -> list[dict[str, Any]]:
        """Search the vector index for similar Fact nodes."""
        if query_embedding is None:
            return []

        # Base query with optional namespace filter
        where_clause = ""
        params: dict[str, Any] = {"embedding": query_embedding, "limit": limit}

        if filters and "namespace" in filters:
            where_clause = "WHERE f.namespace = $namespace"
            params["namespace"] = filters["namespace"]

        cypher = (
            f"CALL db.index.vector.queryNodes('{self.VECTOR_INDEX_NAME}', "
            f"$limit, $embedding) "
            f"YIELD node, score "
            f"MATCH (node:Fact) "
            f"{where_clause} "
            f"RETURN node.id AS fact_id, node.statement AS statement, "
            f"node.predicate AS predicate, node.epistemic_state AS epistemic_state, "
            f"score "
            f"ORDER BY score DESC"
        )

        try:
            with self.driver.session() as s:
                result = s.run(cypher, **params)
                return [
                    {
                        "fact_id": r["fact_id"],
                        "statement": r["statement"],
                        "predicate": r["predicate"],
                        "epistemic_state": r["epistemic_state"],
                        "score": r["score"],
                    }
                    for r in result
                ]
        except Exception as e:
            logger.warning("Vector search failed (degrading to BM25): %s", e)
            return []

    def count(self) -> int:
        """Count Fact nodes with embeddings."""
        with self.driver.session() as s:
            result = s.run(
                "MATCH (f:Fact) WHERE f.embedding IS NOT NULL "
                "RETURN count(f) AS c"
            )
            record = result.single()
            return record["c"] if record else 0
