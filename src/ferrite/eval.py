"""Ferrite eval harness — recall@5, recall@10, MRR against live system.

Per spec §13.2 (A5):
- 30+ queries mined from real session history
- Each query: text, expected canonical entity IDs and/or content substrings,
  query class (entity lookup / temporal / multi-hop / paraphrase / inject)
- No retrieval change ships without before/after eval delta
- Every real-world recall failure becomes a new eval query before it's fixed
- The set only grows
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_QUERIES_FILE = Path(__file__).parent.parent.parent / "eval" / "queries.yaml"


def load_queries(queries_file: Optional[Path] = None) -> list[dict]:
    """Load eval queries from YAML file."""
    path = queries_file or DEFAULT_QUERIES_FILE
    if not path.exists():
        logger.warning("Queries file not found: %s", path)
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("queries", []) if isinstance(data, dict) else data


def _compute_recall_at_k(
    results: list[dict],
    expected_ids: list[str],
    k: int,
) -> float:
    """Compute recall@k: fraction of expected IDs in top-k results."""
    if not expected_ids:
        return 1.0  # No expectations → perfect recall
    top_k = results[:k]
    result_ids = {r.get("id", "") for r in top_k}
    hits = len(set(expected_ids) & result_ids)
    return hits / len(expected_ids)


def _compute_mrr(results: list[dict], expected_ids: list[str]) -> float:
    """Compute Mean Reciprocal Rank: 1/rank of first relevant result."""
    if not expected_ids:
        return 1.0
    for i, r in enumerate(results):
        if r.get("id", "") in expected_ids:
            return 1.0 / (i + 1)
    return 0.0


def _compute_substring_match(results: list[dict], expected_substrings: list[str]) -> bool:
    """Check if any result contains any expected substring."""
    if not expected_substrings:
        return True
    for r in results:
        text = json.dumps(r, default=str).lower()
        for substr in expected_substrings:
            if substr.lower() in text:
                return True
    return False


def run_eval(
    driver,
    embedder=None,
    queries_file: Optional[Path] = None,
    k_values: list[int] | None = None,
) -> dict:
    """Run the eval harness against the live system.

    For each query, runs search_facts and computes:
    - recall@5, recall@10
    - MRR
    - Substring match accuracy

    Returns aggregated metrics.
    """
    from .query import hybrid_search, search_facts
    from .tempr import tempr_search

    if k_values is None:
        k_values = [5, 10]

    queries = load_queries(queries_file)
    if not queries:
        return {
            "error": "No queries loaded",
            "queries_file": str(queries_file or DEFAULT_QUERIES_FILE),
        }

    total_recall = {k: 0.0 for k in k_values}
    total_mrr = 0.0
    total_substring = 0.0
    per_query: list[dict] = []
    total_time = 0.0

    for q in queries:
        query_text = q["text"]
        expected_ids = q.get("expected_entity_ids", [])
        expected_substrings = q.get("expected_substrings", [])
        query_class = q.get("class", "entity_lookup")

        start = time.time()

        # Use TEMPR if available, else hybrid
        try:
            results = tempr_search(
                driver, query_text, embedder=embedder, limit=max(k_values)
            )
        except Exception:
            if embedder is not None:
                results = hybrid_search(
                    driver, query_text, embedder, limit=max(k_values)
                )
            else:
                results = search_facts(
                    driver, query_text, limit=max(k_values)
                )

        elapsed = time.time() - start
        total_time += elapsed

        # Compute metrics
        recall_scores = {
            k: _compute_recall_at_k(results, expected_ids, k)
            for k in k_values
        }
        mrr = _compute_mrr(results, expected_ids)
        substring_match = _compute_substring_match(
            results, expected_substrings
        )

        for k in k_values:
            total_recall[k] += recall_scores[k]
        total_mrr += mrr
        total_substring += 1.0 if substring_match else 0.0

        per_query.append({
            "query": query_text,
            "class": query_class,
            "result_count": len(results),
            "recall": recall_scores,
            "mrr": mrr,
            "substring_match": substring_match,
            "elapsed_ms": round(elapsed * 1000, 1),
        })

    n = len(queries)
    return {
        "total_queries": n,
        "recall": {f"recall@{k}": round(total_recall[k] / n, 4) for k in k_values},
        "mrr": round(total_mrr / n, 4),
        "substring_accuracy": round(total_substring / n, 4),
        "total_time_ms": round(total_time * 1000, 1),
        "avg_query_ms": round(total_time / n * 1000, 1) if n > 0 else 0,
        "per_query": per_query,
    }


def health_check(queries_file: Optional[Path] = None) -> dict:
    """Verify the eval harness is runnable (§8.2, F-5).

    Checks that queries.yaml parses and has valid structure.
    Does NOT run the eval — just verifies the harness is intact.
    """
    path = queries_file or DEFAULT_QUERIES_FILE
    try:
        queries = load_queries(path)
        if not queries:
            return {"status": "warning", "message": "No queries in file"}
        # Validate structure
        for i, q in enumerate(queries):
            if "text" not in q:
                return {
                    "status": "error",
                    "message": f"Query {i} missing 'text' field",
                }
        return {
            "status": "ok",
            "queries": len(queries),
            "file": str(path),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
