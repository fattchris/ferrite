"""LLM extraction: prompt construction, response parsing, and validation."""

import json
import logging
import re
from typing import Callable, Optional

from .vocab import PREDICATE_VOCAB, is_valid_predicate

logger = logging.getLogger(__name__)


EXTRACTION_SYSTEM_PROMPT = """You are a knowledge graph extraction engine.
Given content, extract entities and facts as JSON.

Rules:
- Entities: objects, people, organizations, concepts, technologies.
- Facts: relationships between entities (or entity + literal value).
- Each fact has a subject (entity name), a predicate from the controlled vocabulary,
  an object (entity name or literal value), and metadata.
- If the content states when something became true, set valid_at to that date (ISO 8601).
- If the content implies negation, set negation to true.
- Only use predicates from the controlled vocabulary listed below.
- If a relationship doesn't fit any predicate, skip it.

Respond with ONLY a JSON object. No prose, no markdown fences.

JSON schema:
{
  "entities": [
    {"name": "string", "type": "entity|concept", "summary": "string"}
  ],
  "facts": [
    {"subject": "entity_name", "predicate": "vocab_entry_id",
     "object": "string", "object_type": "entity|literal",
     "certainty": "stated|inferred|speculative",
     "assertion_source": "user|tool_result|model",
     "valid_at": "ISO date or null", "negation": false}
  ]
}
"""


def build_extraction_prompt(content: str, vocab: dict) -> str:
    """Build the user-facing extraction prompt with controlled vocabulary list."""
    vocab_lines = []
    for pred_id, meta in vocab.items():
        func_label = "functional" if meta["functional"] else "non-functional"
        desc = meta.get("description", "")
        vocab_lines.append(f"  - {pred_id} ({func_label}): {desc}")

    vocab_text = "\n".join(vocab_lines)

    prompt = f"""Extract entities and facts from the following content.

Controlled predicate vocabulary (use ONLY these predicates):
{vocab_text}

Content:
---
{content}
---

Return JSON only.
"""
    return prompt


def normalize_literal(value: str) -> str:
    """Normalize a literal value: lowercase, trim, collapse whitespace,
    strip trailing punctuation."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[.,;:!?]+$", "", s)
    return s.strip()


def _extract_json_from_response(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences and prose."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON block in markdown fences
    fence_match = re.search(r"```(?:json)?\s*\n?(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))

    # Try to find any JSON object
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        return json.loads(brace_match.group(0))

    raise ValueError(f"Could not extract JSON from response: {text[:200]}")


def parse_extraction_response(response: str) -> dict:
    """Parse and validate the LLM extraction response.

    Validates:
    - JSON structure (entities + facts arrays)
    - Each fact's predicate is in controlled vocabulary
    - Required fields are present

    Returns validated extraction dict.
    """
    data = _extract_json_from_response(response)

    if "entities" not in data:
        data["entities"] = []
    if "facts" not in data:
        data["facts"] = []

    # Validate entities
    validated_entities = []
    for ent in data.get("entities", []):
        if not isinstance(ent, dict) or "name" not in ent:
            logger.warning(f"Skipping invalid entity: {ent}")
            continue
        ent.setdefault("type", "entity")
        ent.setdefault("summary", "")
        if ent["type"] not in ("entity", "concept"):
            ent["type"] = "entity"
        validated_entities.append(ent)

    # Validate facts
    validated_facts = []
    for fact in data.get("facts", []):
        if not isinstance(fact, dict):
            continue
        if "subject" not in fact or "predicate" not in fact or "object" not in fact:
            logger.warning(f"Skipping invalid fact (missing fields): {fact}")
            continue

        if not is_valid_predicate(fact["predicate"]):
            logger.warning(
                f"Skipping fact with unknown predicate '{fact['predicate']}'"
            )
            continue

        fact.setdefault("object_type", "entity")
        fact.setdefault("certainty", "stated")
        fact.setdefault("assertion_source", "model")
        fact.setdefault("valid_at", None)
        fact.setdefault("negation", False)

        if fact["object_type"] not in ("entity", "literal"):
            fact["object_type"] = "entity"
        if fact["certainty"] not in ("stated", "inferred", "speculative"):
            fact["certainty"] = "stated"
        if fact["assertion_source"] not in ("user", "tool_result", "model"):
            fact["assertion_source"] = "model"

        # Normalize literal objects
        if fact["object_type"] == "literal":
            fact["object"] = normalize_literal(fact["object"])

        validated_facts.append(fact)

    return {"entities": validated_entities, "facts": validated_facts}


def extract(content: str, llm_client: Optional[Callable] = None) -> dict:
    """Orchestrate LLM extraction: build prompt, call LLM, parse response.

    Args:
        content: The text content to extract from.
        llm_client: A callable that takes (system_prompt, user_prompt) and returns
                    the LLM response string. If None, returns empty extraction.

    Returns parsed and validated extraction dict.
    """
    prompt = build_extraction_prompt(content, PREDICATE_VOCAB)

    if llm_client is None:
        logger.warning("No LLM client provided; returning empty extraction.")
        return {"entities": [], "facts": []}

    response_text = llm_client(EXTRACTION_SYSTEM_PROMPT, prompt)

    # Guard against None / empty responses from the LLM
    if not response_text or not isinstance(response_text, str) or not response_text.strip():
        logger.warning("LLM returned empty/None response; skipping extraction.")
        return {"entities": [], "facts": []}

    try:
        return parse_extraction_response(response_text)
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"LLM response parse failed: {e}. Response: {response_text[:200]}")
        return {"entities": [], "facts": []}
