"""LLM extraction: prompt generation, response parsing, and validation.""""

import json
import logging
from typing import Any

import httpx

from ferrite.config import get_settings
from ferrite.models import ExtractionResult, ExtractedEntity, ExtractedFact
from ferrite.vocab import is_valid_predicate

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are a knowledge graph extraction engine. Extract entities and facts from the provided content.
Return ONLY valid JSON matching this schema:
{
  "entities": [{"name": "string", "type": "entity|concept", "summary": "string"}],
  "facts": [{"subject": "entity_name", "predicate": "vocab_entry_id", "object": "string", "object_type": "entity|literal", "certainty": "stated|inferred|speculative", "assertion_source": "user|tool_result|model", "valid_at": "ISO date or null", "negation": false}]
}

Use only these allowed predicates: works_at, version_is, runs_on, uses, depends_on, related_to, manages, owns, funded_by, member_of, part_of, supports, integrates_with, competes_with, built_by, maintained_by, designed_by, deployed_at, hosts, contains, produces, consumes, replaced_by, derived_from, specified_by, classified_as, instance_of, alias_of, released_on, occurred_on, ceo_of, capital_is, located_in, founded_in, died_in, born_in, state_is, author_is, parent_is.

If valid_at is not explicitly stated in content, set it to null.
""""


async def extract(content: str, content_type: str = "text/plain") -> ExtractionResult:
    """Call LLM to extract entities and facts from content.""""
    settings = get_settings()

    payload = {
        "model": settings.EXTRACTION_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": content}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.LLM_BASE_URL}/chat/completions",
                json=payload,
                headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            content_str = data["choices"][0]["message"]["content"]
            raw = json.loads(content_str)
            return _parse_and_validate(raw)
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return ExtractionResult()


def _parse_and_validate(raw: dict[str, Any]) -> ExtractionResult:
    """Parse and validate extraction response.""""
    entities = []
    for e in raw.get("entities", []):
        try:
            entities.append(ExtractedEntity(
                name=e["name"],
                type=e.get("type", "entity"),
                summary=e.get("summary")
            ))
        except Exception:
            continue

    facts = []
    for f in raw.get("facts", []):
        if not is_valid_predicate(f.get("predicate", "")):
            logger.warning(f"Invalid predicate: {f.get('predicate')}")
            continue
        try:
            facts.append(ExtractedFact(
                subject=f["subject"],
                predicate=f["predicate"],
                object=f["object"],
                object_type=f.get("object_type", "entity"),
                certainty=f.get("certainty", "stated"),
                assertion_source=f.get("assertion_source", "model"),
                valid_at=f.get("valid_at"),
                negation=f.get("negation", False)
            ))
        except Exception:
            continue

    return ExtractionResult(entities=entities, facts=facts)


def generate_statement(subject: str, predicate: str, obj: str) -> str:
    """Generate canonical statement string.""""
    return f"{subject} {predicate} {obj}"
