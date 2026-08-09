# Ferrite Phase 1 — Core Schema + Ingestion API

## Objective

Build the first working slice of the Ferrite temporal KG: the data model,
Neo4j schema, entity canonicalization, and a FastAPI ingestion endpoint
that accepts content, extracts entities/facts via LLM, and writes to Neo4j.

## Spec (Key Sections)

### Knowledge Model — Facts are Nodes (§3.1)

Facts are **nodes**, not edges. Entities are referents — no epistemic state.

**Fact node properties:**
- `id`: UUID
- `predicate`: string (vocab entry ID, e.g. `works_at`, `runs_on`)
- `statement`: string (canonical: `"{subject} {predicate} {object}"`, materialized at write time)
- `functional`: boolean (looked up from vocab config at write time)
- `certainty`: enum `stated | inferred | speculative`
- `epistemic_state`: enum `active | contradicted | superseded` (default `active`)
- `assertion_source`: enum `user | tool_result | model`
- `valid_at`: datetime (when fact became true in world)
- `valid_at_inferred`: boolean
- `invalid_at`: datetime (null = still valid)
- `recorded_at`: datetime (when Ferrite learned it)
- `namespace`: enum `shared | personal` (default `shared`)

**Entity node (global — no namespace):**
- `id`: UUID
- `type`: enum `entity | concept`
- `name`: string (display name, canonical form)
- `summary`: string (LLM-generated, from shared-namespace facts only)

### Edge Types (§3.2)

| Type | Description |
|---|---|
| `SUBJECT` | Fact → Entity (subject of triple) |
| `OBJECT` | Fact → Entity or Fact → Literal {value, type} |
| `SOURCED_FROM` | Fact → Episode (provenance) |
| `SUPERSEDES` | Fact → Fact (new supersedes old) |
| `CONTRADICTS` | Fact → Fact (conflicting, neither invalidated) |
| `SUPPORTS` | Observation → Fact |
| `CONSOLIDATES` | Observation → Fact |
| `ALIAS` | Entity → Alias {norm} |
| `MERGED_INTO` | Entity → Entity |
| `CURATED_FOR` | Mental Model → Entity/Concept |
| `MEMBER_OF` | Entity → Group/namespace |

Deleted from v2: `FACT` edge (facts are nodes now), `RELATED_TO` edge
(loose associations are Facts with `related_to` predicate, non-functional).

### Temporal Model (§3.3 — Bitemporal)

Two timestamps on every Fact:
- `recorded_at`: when Ferrite learned it (set by ingestion)
- `valid_at` / `invalid_at`: when true in the world (extractor if content states it; else `valid_at = recorded_at`, `valid_at_inferred = true`)

Two query modes via `get_history`:
- `as_of_knowledge(T)`: filter `recorded_at <= T` (what did we know)
- `as_of_world(T)`: filter `valid_at <= T < coalesce(invalid_at, ∞)` (what was true)

**Supersession rules:**
- Functional predicate, different object → SUPERSEDES (new supersedes old; old gets `invalid_at = new.valid_at`)
- Same predicate + object, negation flag → CONTRADICTS (both flagged `contradicted`)
- Non-functional predicate → coexist, no edge
- Facts are NEVER deleted. Old Fact gets `invalid_at` and SUPERSEDES edge.

### Supersession Scope

`(canonical subject, predicate)` — indexed lookup, not graph scan.

### Controlled Predicate Vocabulary (§7.2.2)

~40 entries. Each has `functional: boolean`:
- Functional (`true`): `works_at`, `version_is`, `runs_on` — one true value at a time
- Non-functional (`false`): `uses`, `depends_on`, `related_to` — coexist

### Entity Canonicalization (§7.2.3)

1. Normalize: lowercase, strip punctuation, collapse whitespace, normalize separators
2. Exact alias lookup: `(:Entity)-[:ALIAS]->(:Alias {norm})` index
3. Embedding match: ANN search, ≥0.95 auto-merge, 0.80-0.95 LLM adjudication, <0.80 new
4. Merges are additive: `MERGED_INTO` edges, never destructive

### Extraction Prompt Schema (§7.2.4)

```json
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
```

### Literal Object Normalization (§7.2.5)

- Strings: lowercase, trim, collapse whitespace, strip trailing punctuation
- Numbers: parse to float, compare numerically

### API (§4.2 — MCP Tools, simplified for MVP)

```
store(content: str, content_type: str, source: object, namespace?: str)
  → Queue episode for ingestion (async)
  → Returns: {episode_id, status: "queued"}

search(query: str, namespace?: str, limit?: int)
  → BM25 + semantic hybrid search on Fact.statement
  → Returns: [{id, statement, certainty, source, valid_at}]

get_entity(id: str)
  → Full node with edges, provenance, temporal history
  → Edges filtered by namespace

get_history(id: str, at_time?: datetime, mode?: "knowledge"|"world")
  → Temporal query: as_of_knowledge or as_of_world

health()
  → System health: Neo4j, Redis, queue depth
```

### Rate Limiting (§4.1)

Per API key, token bucket: 100 req/min read, 20 req/min write. Admin keys exempt.

## Stack

- Python 3.11+, FastAPI, uvicorn
- Neo4j (neo4j Python driver)
- Redis (redis-py)
- Embeddings via LiteLLM at http://localhost:4000/v1
- Docker Compose (single container for MVP)

## What to Build

### Files to Create

1. `src/ferrite/__init__.py` — package init, version
2. `src/ferrite/config.py` — Settings (env vars: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, REDIS_URL, LLM_API_KEY, LLM_BASE_URL, NAMESPACE_DEFAULT)
3. `src/ferrite/models.py` — Pydantic models for Fact, Entity, Episode, Alias, Observation
4. `src/ferrite/vocab.py` — Controlled predicate vocabulary with functional flags
5. `src/ferrite/schema.py` — Neo4j constraint/index creation (Cypher DDL)
6. `src/ferrite/canonicalize.py` — Entity normalization + alias resolution
7. `src/ferrite/extractor.py` — LLM extraction prompt + response parsing + validation
8. `src/ferrite/ingestion.py` — Queue → extract → canonicalize → write to Neo4j
9. `src/ferrite/temporal.py` — Supersession/contradiction detection logic
10. `src/ferrite/api.py` — FastAPI app with store/search/get_entity/get_history/health endpoints
11. `src/ferrite/main.py` — Entry point: uvicorn launch + schema init
12. `tests/test_models.py` — Test Fact/Entity model validation
13. `tests/test_canonicalize.py` — Test normalization + alias resolution
14. `tests/test_temporal.py` — Test supersession/contradiction logic
15. `tests/test_vocab.py` — Test functional predicate lookup

### Key Constraints

- Every Cypher query goes through a query builder that injects namespace filters
- Entity nodes are global — never attach namespace to Entity nodes
- Facts carry the namespace
- No `RELATED_TO` edge type — relationships are Facts with `related_to` predicate
- Facts are never deleted — old facts get `invalid_at` and SUPERSEDES edge
- `assertion_source` field on every fact (defense against graph poisoning)
