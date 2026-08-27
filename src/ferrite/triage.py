"""Episode triage: decide how an episode gets ingested WITHOUT an LLM.

Tiers (cheapest first):
  SKIP      — boilerplate/tool-output/status noise; nothing extractable
  STRUCTURED— content already carries machine-checkable assertions
              (explicit triple syntax from the plugin tool call)
  PATTERN   — simple declarative SVO sentences mapped to the predicate
              vocab via embedding cosine
  LLM       — ambiguous remainder; escalate to GLM extraction

The pipeline consumes this in process_episode().
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TriageVerdict:
    tier: str                      # skip | structured | pattern | llm
    confidence: float = 0.0        # 0..1
    reason: str = ""
    sentences: list[str] = field(default_factory=list)  # for pattern tier


# --- heuristics -------------------------------------------------------------

_BOILERPLATE_PATTERNS = [
    r"^\s*(ok|okay|done|got it|thanks|thank you|nice|cool|great|perfect)[.!.?]*\s*$",
    r"^\s*[/?]\w+",                                   # slash commands
    r"(?i)^\s*(running|ran)\b.*\b(test|tests|build|lint)",
    r"(?i)^(git|docker|kubectl|pip|npm|curl|ls|cd|cat|grep)\b",  # raw commands
    r"(?i)^\s*\d+\s*(passed|failed|error|warning)",  # test summaries
    r"^[A-Z][A-Z ]{1,20}:\s\S",                     # "STATUS: ..." labeled lines
    r"(?i)(exit code|stdout|stderr)\b",
]

_CONTENT_MIN_CHARS = 40          # shorter than this has no extraction value
_ALNUM_RATIO_MIN = 0.55          # base64/hex/hash dumps
_MAX_TOKENS = 400                # very long content likely a doc; let LLM see it

_TRIPLE_RE = re.compile(
    r'\{\s*"subject"\s*:|ferrite_assert\(|\[\[\s*.+\s*\|\s*.+\s*\|\s*.+\s*\]\]'
)

_DECLARATIVE_STARTERS = (
    "is ", "are ", "was ", "were ", "uses ", "runs ", "lives ",
    "depends on ", "works at ", "located ", "version",
)


def _is_boilerplate(text: str) -> str | None:
    """Return a reason string if text is junk, else None."""
    stripped = text.strip()
    if len(stripped) < _CONTENT_MIN_CHARS:
        return "too short"
    alnum = sum(ch.isalnum() or ch.isspace() for ch in stripped) / max(len(stripped), 1)
    if alnum < _ALNUM_RATIO_MIN:
        return "non-natural-text (code/hash dump)"
    for pat in _BOILERPLATE_PATTERNS:
        # pattern check per line — one junk line doesn't doom the episode,
        # but if >60% of lines match, treat whole thing as noise
        lines = [l for l in stripped.splitlines() if l.strip()]
        if not lines:
            return "empty"
        hits = sum(1 for l in lines if re.search(pat, l))
        if hits / len(lines) > 0.6:
            return f"boilerplate: {pat[:30]}"
    return None


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if 25 < len(p.strip()) < 300]


def triage(text: str, content_type: str = "text") -> TriageVerdict:
    if content_type != "text":
        return TriageVerdict("llm", 1.0, f"non-text type {content_type}")

    junk_reason = _is_boilerplate(text)
    if junk_reason:
        return TriageVerdict("skip", 0.95, junk_reason)

    # Explicit structured triples supplied by the agent itself
    if _TRIPLE_RE.search(text):
        return TriageVerdict("structured", 0.99, "explicit triple payload present")

    sents = _split_sentences(text)
    candidates = [
        s for s in sents
        if any(s.lower().startswith(st) or st in s.lower()[:60] for st in _DECLARATIVE_STARTERS)
    ]
    if len(candidates) >= len(sents) * 0.5 and candidates:
        return TriageVerdict("pattern", 0.7, "declarative-dominant", candidates)

    return TriageVerdict("llm", 0.5, "ambiguous prose")
