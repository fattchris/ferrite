"""Ingest quality gates (§7.2.1, A10).

Two gates, both cheap:

Session gate (in the session-end hook, before enqueue):
- ≥ 2 user turns
- Session did not end in an error/reverted state
- Failed sessions are still stored as searchable episodes (transcript
  retrieval works) but skip fact extraction — no beliefs derived from
  broken runs.

Assertion gate (in the extraction prompt + schema):
- Every fact carries assertion_source: user | tool_result | model
- Extraction instruction: derive facts only from user statements and
  successful tool results. Model speculation/plans are not facts.
- model-sourced facts are excluded from consolidation and ranked below
  user/tool_result in recall.

This is the primary defense against graph poisoning from agent failure
loops. It costs zero extra LLM calls.
"""

import logging

logger = logging.getLogger(__name__)


def session_gate(
    user_turn_count: int,
    ended_in_error: bool = False,
    reverted: bool = False,
) -> tuple[bool, str]:
    """Session-level quality gate (§7.2.1).

    Returns (should_extract, reason).
    - If the session has < 2 user turns, skip extraction.
    - If the session ended in error or was reverted, skip extraction.
    - The episode is still stored as searchable content regardless.

    Args:
        user_turn_count: Number of user turns in the session.
        ended_in_error: Whether the session ended in an error state.
        reverted: Whether the session was reverted/cancelled.

    Returns:
        (True, "passed") if extraction should proceed,
        (False, reason) if extraction should be skipped.
    """
    if user_turn_count < 2:
        reason = f"session has only {user_turn_count} user turns (minimum 2)"
        logger.info(f"Session gate: skipping extraction — {reason}")
        return False, reason

    if ended_in_error:
        reason = "session ended in error state"
        logger.info(f"Session gate: skipping extraction — {reason}")
        return False, reason

    if reverted:
        reason = "session was reverted"
        logger.info(f"Session gate: skipping extraction — {reason}")
        return False, reason

    return True, "passed"


def assertion_gate(fact_data: dict) -> bool:
    """Assertion-level quality gate (§7.2.1).

    Checks that a fact's assertion_source is valid.
    model-sourced facts are allowed but flagged for downranking.

    Returns True if the fact passes the gate.
    """
    assertion_source = fact_data.get("assertion_source", "model")

    valid_sources = {"user", "tool_result", "model"}
    if assertion_source not in valid_sources:
        logger.warning(
            f"Assertion gate: invalid assertion_source '{assertion_source}' "
            f"for fact: {fact_data.get('subject', '?')} "
            f"{fact_data.get('predicate', '?')}"
        )
        return False

    return True


def should_rank_fact(fact_data: dict) -> int:
    """Ranking priority for facts based on assertion_source (§7.2.1).

    user-sourced > tool_result-sourced > model-sourced.
    Returns an integer priority (lower = higher priority).
    """
    assertion_source = fact_data.get("assertion_source", "model")
    if assertion_source == "user":
        return 0
    elif assertion_source == "tool_result":
        return 1
    else:
        return 2


def should_consolidate_fact(fact_data: dict) -> bool:
    """Whether a fact should be included in consolidation (§7.2.1).

    model-sourced facts are excluded from consolidation.
    """
    assertion_source = fact_data.get("assertion_source", "model")
    # model-sourced facts excluded from consolidation per §7.2.1
    return assertion_source != "model"
