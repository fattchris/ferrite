"""Deterministic pattern extraction: declarative sentences to facts.

No LLM. Subject/object spans come from lightweight grammatical heuristics;
predicate selection is embedding cosine similarity against the controlled
vocabulary descriptions (reuses the existing embedding_func).
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from .models import ExtractedEntity, ExtractedFact

# sentence templates: regex captures subject + object, keyed by candidate verb phrase
_OBJ = "(?P<obj>[\\w .\\'/:-]{2,60}?)(?: as | for | with | using | because | since |,|;|\\.|$)"

_TEMPLATES = [

    # "X uses Y", "X runs Y"
    (re.compile(r"^(?P<subj>[A-Z][\w .'-]{1,40}?)\s+(?P<verb>uses|runs)\s+" + _OBJ), {"uses": "uses", "runs": "runs_on"}),
    # "X depends on Y"
    (re.compile(r"^(?P<subj>[A-Z][\w .'-]{1,40}?)\s+depends on\s+" + _OBJ), None),
    # "X works at Y"
    (re.compile(r"^(?P<subj>[A-Z][\w .'-]{1,40}?)\s+works at\s+" + _OBJ), None),
    # "version of X is Y" / "the version of X is Y"
    (re.compile(r"^(?:the |The )?version of (?P<subj>[A-Z][\w .'-]{0,38}?) is (?P<obj>[0-9][\w.\-]*)"), None),
    # "X is located in Y"
    (re.compile(r"^(?P<subj>[A-Z][\w .'-]{1,40}?)\s+is located in\s+(?P<obj>[\w .,-]{2,60}?)[.,;]$"), None),
    # "X version is 1.2.3"
    (re.compile(r"^(?P<subj>[A-Z][\w .'-]{1,40}?)\s+version is (?P<obj>[0-9][\w.\-]*)$"), None),
    # copula: "X is a/an <type>" — weak; only map when object looks like a category noun
    (re.compile(r"^(?P<subj>[A-Z][\w .'-]{1,40}?)\s+is an?\s+(?P<obj>[\w -]{3,50}?)[.,;]$"), None),
]

# generic predicate for copula forms resolved by cosine below
_COPULA = re.compile(r"^(?P<subj>[A-Z][\w .'-]{1,40}?)\s+is an?\s+(?P<obj>.{3,60}?)[.,;]?$")

_NAMES_RE = re.compile(r"[A-Za-z]")


def extract_patterns(text: str, embedding_func: Callable[[str], list[float]],
                     vocab: dict) -> list[ExtractedFact]:
    """Return facts extractable without any LLM call."""
    from .extractor import normalize_literal  # reuse literal cleanup

    facts: list[ExtractedFact] = []
    # sentence split mirrors triage._split_sentences
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    sents = [p.strip() for p in parts if 12 < len(p.strip()) < 300]

    for sent in sents:
        matched = False
        for rx, mapping in _TEMPLATES:
            m = rx.match(sent)
            if not m:
                continue
            subj = m.group("subj").strip()
            obj = m.group("obj").strip()
            if not _NAMES_RE.search(subj) or not _NAMES_RE.search(obj):
                continue
            if mapping is not None:
                pred = mapping.get(m.group("verb"))
            else:
                # template-specific predicates
                if "depends on" in sent.lower():
                    pred = "depends_on"
                elif "works at" in sent.lower():
                    pred = "works_at"
                elif "located in" in sent.lower():
                    pred = "located_in"
                elif sent.lower().startswith("version of") or "version is" in sent.lower():
                    pred = "version_is"
                else:
                    pred = _best_pred_by_cosine(f"{sent}", vocab, embedding_func)
            if pred:
                obj_type = "literal" if pred == "version_is" or not obj[:1].isupper() else "entity"
                facts.append(ExtractedFact(
                    subject=subj, predicate=pred,
                    object=normalize_literal(obj) if obj_type == "literal" else obj.rstrip("."),
                    object_type=obj_type,
                    certainty="inferred",
                    assertion_source="model",
                ))
                matched = True
                break
        # unmatched: nothing (LLM tier will see the full episode anyway)
    return facts


def _best_pred_by_cosine(text: str, vocab: dict,
                         embedding_func: Callable[[str], list[float]]) -> Optional[str]:
    import math
    try:
        q = embedding_func(text)
    except Exception:
        return None
    best_id, best_sim = None, 0.30  # floor: below this we'd rather skip than guess
    for pid, meta in vocab.items():
        d = meta.get("description") or pid.replace("_", " ")
        try:
            e = embedding_func(d)
        except Exception:
            continue
        dot = sum(a * b for a, b in zip(q, e))
        nq = math.sqrt(sum(a * a for a in q)) or 1e-9
        ne = math.sqrt(sum(b * b for b in e)) or 1e-9
        sim = dot / (nq * ne)
        if sim > best_sim:
            best_id, best_sim = pid, sim
    return best_id