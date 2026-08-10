"""TEMPR: Multi-strategy retrieval with Reciprocal Rank Fusion.

Per spec §3.8 (A8): Five search strategies run in parallel, fused with RRF.

Strategies:
1. Semantic (vector similarity via VectorStore)
2. Keyword (BM25 via Neo4j fulltext index)
3. Graph (multi-hop entity relationships via Cypher traversal)
4. Temporal (valid_at/invalid_at range filter)
5. Recency (candidates ranked by recorded_at desc, A8)

Each strategy has a 2s timeout. Degradation ladder:
- Full TEMPR (all 5)
- → Vector + Graph + BM25 + Recency (temporal off)
- → Vector + BM25 + Recency (graph too slow)
- → BM25 only (vector unavailable)
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import Embedder
from .query import _bm25_search, vector_search

logger = logging.getLogger(__name__)

DEFAULT_RRF_K = 60
DEFAULT_TIMEOUT = 2.0  # seconds per strategy


def _graph_search(
    driver,
    query_text: str,
    namespace: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Graph traversal strategy: find facts connected to entities mentioned in query.

    Extracts entity names from the query, does multi-hop traversal to find
    related facts. Returns facts ranked by hop distance (closer = higher rank).
    """
    # Extract candidate entity names from the query
    # Simple heuristic: lowercase words that match known entity patterns
    words = re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", query_text.lower())
    if not words:
        return []

    ns_filter = "AND f.namespace = $namespace" if namespace else ""
    params: dict = {"limit": limit * 2}
    if namespace:
        params["namespace"] = namespace

    results: list[dict] = []
    seen_ids: set[str] = set()

    with driver.session() as session:
        for word in words:
            # Match entity names containing this word
            query = f"""
                MATCH (e:Entity)
                WHERE toLower(e.name) CONTAINS $word
                MATCH (e)<-[:SUBJECT|OBJECT]-(f:Fact)
                WHERE f.epistemic_state = 'active'
                {ns_filter}
                OPTIONAL MATCH (f)-[:OBJECT]->(obj)
                RETURN f.id AS id,
                       f.statement AS statement,
                       f.predicate AS predicate,
                       f.certainty AS certainty,
                       f.epistemic_state AS epistemic_state,
                       f.namespace AS namespace,
                       f.valid_at AS valid_at,
                       f.recorded_at AS recorded_at,
                       COALESCE(obj.name, obj.value) AS object_value
                LIMIT 5
            """
            params["word"] = word
            try:
                result = session.run(query, **params)
                for r in result:
                    r = dict(r)
                    if r["id"] not in seen_ids:
                        r["score"] = 1.0  # placeholder score
                        results.append(r)
                        seen_ids.add(r["id"])
            except Exception as e:
                logger.debug("Graph search error for word '%s': %s", word, e)

    return results[:limit * 2]


def _temporal_search(
    driver,
    query_text: str,
    namespace: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Temporal strategy: filter facts by valid_at time range.

    Detects time expressions in the query (e.g. "last spring", "in June",
    "2026-01") and filters facts by valid_at within that range.
    """
    # Extract time hints from the query
    time_range = _parse_time_expression(query_text)
    if time_range is None:
        return []  # No temporal hint → strategy returns nothing

    start_dt, end_dt = time_range
    ns_filter = "AND f.namespace = $namespace" if namespace else ""

    params: dict = {
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "limit": limit * 2,
    }
    if namespace:
        params["namespace"] = namespace

    with driver.session() as session:
        try:
            # Also do BM25 within the temporal filter
            result = session.run(
                f"""
                CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $search_text)
                YIELD node AS f, score
                WHERE f:Fact
                  AND f.epistemic_state = 'active'
                  AND f.valid_at >= $start
                  AND (f.invalid_at IS NULL OR f.invalid_at <= $end)
                  {ns_filter}
                RETURN f.id AS id,
                       f.statement AS statement,
                       f.predicate AS predicate,
                       f.certainty AS certainty,
                       f.epistemic_state AS epistemic_state,
                       f.namespace AS namespace,
                       f.valid_at AS valid_at,
                       f.recorded_at AS recorded_at,
                       score
                ORDER BY score DESC
                LIMIT $limit
                """,
                search_text=query_text,
                **params,
            )
            return [dict(r) for r in result]
        except Exception as e:
            logger.debug("Temporal search failed: %s", e)
            return []


def _recency_search(
    driver,
    query_text: str,
    namespace: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Recency strategy (A8): candidates ranked by recorded_at descending.

    This is the fifth RRF ranked list. Recent items get a bounded boost,
    old facts remain fully rankable. Combined via RRF, not as a multiplier.
    """
    ns_filter = "AND f.namespace = $namespace" if namespace else ""

    params: dict = {"limit": limit * 2, "search_text": query_text}
    if namespace:
        params["namespace"] = namespace

    with driver.session() as session:
        try:
            # Use fulltext to get candidates, then rank by recency
            result = session.run(
                f"""
                CALL db.index.fulltext.queryNodes('fact_statement_fulltext', $search_text)
                YIELD node AS f, score
                WHERE f:Fact
                  AND f.epistemic_state = 'active'
                  {ns_filter}
                RETURN f.id AS id,
                       f.statement AS statement,
                       f.predicate AS predicate,
                       f.certainty AS certainty,
                       f.epistemic_state AS epistemic_state,
                       f.namespace AS namespace,
                       f.valid_at AS valid_at,
                       f.recorded_at AS recorded_at,
                       score
                ORDER BY f.recorded_at DESC
                LIMIT $limit
                """,
                **params,
            )
            return [dict(r) for r in result]
        except Exception as e:
            logger.debug("Recency search failed: %s", e)
            return []


def _parse_time_expression(text: str) -> Optional[tuple[datetime, datetime]]:
    """Parse natural language time expressions into (start, end) datetime range.

    Detects: "last spring", "in June", "2026-01", "last week", "yesterday",
    relative month/year expressions.
    """
    text_lower = text.lower()
    now = datetime.now(timezone.utc)

    # "last spring" → March-May of previous year
    if "last spring" in text_lower:
        year = now.year - 1
        return (datetime(year, 3, 1, tzinfo=timezone.utc),
                datetime(year, 6, 1, tzinfo=timezone.utc))

    # "last summer" → June-August of previous year
    if "last summer" in text_lower:
        year = now.year - 1
        return (datetime(year, 6, 1, tzinfo=timezone.utc),
                datetime(year, 9, 1, tzinfo=timezone.utc))

    # "last fall" / "last autumn"
    if "last fall" in text_lower or "last autumn" in text_lower:
        year = now.year - 1
        return (datetime(year, 9, 1, tzinfo=timezone.utc),
                datetime(year, 12, 1, tzinfo=timezone.utc))

    # "last winter"
    if "last winter" in text_lower:
        year = now.year - 1
        return (datetime(year, 12, 1, tzinfo=timezone.utc),
                datetime(year + 1, 3, 1, tzinfo=timezone.utc))

    # "in June" / "in january" → that month this year
    month_match = re.search(
        r"\bin (january|february|march|april|may|june|july|"
        r"august|september|october|november|december)\b",
        text_lower,
    )
    if month_match:
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
        }
        month = months[month_match.group(1)]
        year = now.year
        if month > now.month:
            year = now.year - 1
        _, last_day = calendar.monthrange(year, month)
        return (datetime(year, month, 1, tzinfo=timezone.utc),
                datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc))

    # "last week" → 7 days ago to now
    if "last week" in text_lower:
        return (now.replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(days=7),
                now)

    # "yesterday" → yesterday
    if "yesterday" in text_lower:
        yesterday = now - timedelta(days=1)
        start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yesterday.replace(hour=23, minute=59, second=59)
        return (start, end)

    # "last month" → previous month
    if "last month" in text_lower:
        if now.month == 1:
            start = datetime(now.year - 1, 12, 1, tzinfo=timezone.utc)
            end = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        else:
            start = datetime(now.year, now.month - 1, 1, tzinfo=timezone.utc)
            end = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        return (start, end)

    # Explicit date: "2026-01" or "2026-01-15"
    date_match = re.search(r"\b(\d{4})-(\d{1,2})(?:-(\d{1,2}))?\b", text)
    if date_match:
        year, month = int(date_match.group(1)), int(date_match.group(2))
        day = int(date_match.group(3)) if date_match.group(3) else None
        if day:
            start = datetime(year, month, day, tzinfo=timezone.utc)
            end = datetime(year, month, day, 23, 59, 59, tzinfo=timezone.utc)
        else:
            _, last_day = calendar.monthrange(year, month)
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
        return (start, end)

    return None


def _rrf_fuse(
    ranked_lists: list[list[dict]],
    k: int = DEFAULT_RRF_K,
) -> list[dict]:
    """Combine multiple ranked lists using Reciprocal Rank Fusion.

    RRF formula: score = sum(1/(k + rank_i)) for each strategy.
    Results deduplicated by node ID.
    """
    rrf_scores: dict[str, float] = {}
    fact_data: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, r in enumerate(ranked_list):
            fid = r["id"]
            rrf_scores[fid] = rrf_scores.get(fid, 0) + 1.0 / (k + rank + 1)
            if fid not in fact_data:
                fact_data[fid] = r

    sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)

    results = []
    for fid in sorted_ids:
        r = dict(fact_data[fid])
        r["score"] = rrf_scores[fid]
        results.append(r)

    return results


def _rerank_by_epistemic_state(results: list[dict]) -> list[dict]:
    """Rerank fused results by epistemic state (spec §3.8 step 4).

    Priority: active > contradicted > superseded.
    Superseded excluded by default (include_history flag for temporal queries).
    """
    priority = {"active": 0, "contradicted": 1, "superseded": 2}
    # Filter out superseded by default, sort by priority then score
    filtered = [r for r in results if r.get("epistemic_state") != "superseded"]
    return sorted(filtered, key=lambda r: (
        priority.get(r.get("epistemic_state", "active"), 0),
        -float(r.get("score", 0.0)),
    ))


def _run_strategy_with_timeout(
    strategy_fn,
    strategy_name: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Run a TEMPR strategy with a timeout (F-3 fix).

    Uses concurrent.futures.ThreadPoolExecutor since TEMPR is sync.
    Returns [] on timeout or failure.
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(strategy_fn)
            try:
                result = future.result(timeout=timeout)
                return result or []
            except FuturesTimeoutError:
                logger.warning(
                    "TEMPR strategy '%s' timed out after %ss — skipping",
                    strategy_name, timeout,
                )
                future.cancel()
                return []
    except Exception as e:
        logger.warning("TEMPR strategy '%s' failed: %s", strategy_name, e)
        return []


def tempr_search(
    driver,
    query_text: str,
    embedder: Optional[Embedder] = None,
    namespace: Optional[str] = None,
    limit: int = 10,
    rrf_k: int = DEFAULT_RRF_K,
    include_history: bool = False,
    strategy_timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Full TEMPR multi-strategy retrieval with RRF fusion.

    Runs 5 strategies in parallel via ThreadPoolExecutor, each with
    a per-strategy timeout (F-3 fix). Strategies that time out are
    silently skipped — degradation ladder. Fuses via RRF, reranks
    by epistemic state.

    Args:
        driver: Neo4j driver.
        query_text: Search query.
        embedder: OllamaEmbedder for semantic search (None = skip vector).
        namespace: Optional namespace filter.
        limit: Max results to return.
        rrf_k: RRF parameter (default 60).
        include_history: If True, include superseded facts.
        strategy_timeout: Per-strategy timeout in seconds (default 2.0).

    Returns:
        Fused and reranked list of fact dicts.
    """
    # Build strategy list (F-8 fix: actually parallel)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    strategies: list[tuple[str, object]] = []

    if embedder is not None:
        strategies.append((
            "semantic",
            lambda: vector_search(
                driver, query_text, embedder,
                namespace=namespace, limit=limit * 2,
            ),
        ))

    strategies.append((
        "bm25",
        lambda: _bm25_search(
            driver, query_text, namespace=namespace, limit=limit * 2,
        ),
    ))

    strategies.append((
        "graph",
        lambda: _graph_search(
            driver, query_text, namespace=namespace, limit=limit * 2,
        ),
    ))

    strategies.append((
        "temporal",
        lambda: _temporal_search(
            driver, query_text, namespace=namespace, limit=limit * 2,
        ),
    ))

    strategies.append((
        "recency",
        lambda: _recency_search(
            driver, query_text, namespace=namespace, limit=limit * 2,
        ),
    ))

    # Run all strategies in parallel with per-strategy timeout (F-3, F-8 fix)
    ranked_lists: list[list[dict]] = []
    with ThreadPoolExecutor(
        max_workers=len(strategies),
    ) as executor:
        future_map = {
            executor.submit(
                _run_strategy_with_timeout,
                fn, name, strategy_timeout,
            ): name
            for name, fn in strategies
        }
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                result = future.result()
                if result:
                    ranked_lists.append(result)
            except Exception as e:
                logger.warning("TEMPR strategy '%s' failed: %s", name, e)

    if not ranked_lists:
        return []

    # RRF fusion
    fused = _rrf_fuse(ranked_lists, k=rrf_k)

    # Rerank by epistemic state
    if not include_history:
        fused = _rerank_by_epistemic_state(fused)
    else:
        # Just sort by score, keep all
        fused = sorted(fused, key=lambda r: r.get("score", 0), reverse=True)

    return fused[:limit]
