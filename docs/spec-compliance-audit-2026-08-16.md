# Ferrite v3 Spec Compliance Audit — 2026-08-16

**Method:** every §-section of `~/ferrite-spec-v3.md` checked against live
system state (Neo4j queries, Docker, API probes, source inspection) — not
build logs. Build reports claim things; this doc verifies them.

**System at audit time:** 1,038 Hermes sessions mid-backfill (session 459,
14,813 facts, all extracted on GLM-5.2 via LiteLLM :4000).

---

## Verdict

**Core memory system: COMPLIANT.** Every §1 pillar is live and verified:
bitemporal facts, epistemic states, reified facts, canonicalization,
controlled predicates, provenance chains, MCP server, key management,
rate limiting, eval harness, file repository.

**Gaps are concentrated in the "product" layer** (deferred items), plus
operational debt from the backfill and two deviations worth deciding on.

---

## §-by-§ results

| § | Area | Status | Evidence |
|---|------|--------|----------|
| 3.1–3.2 | Reified facts, edge types | ✅ | Fact nodes with SUBJECT/OBJECT edges; ALIAS/SUPERSEDES/CONTRADICTS/SUPPORTS/SOURCED_FROM present |
| 3.3 | Bitemporal | ✅ | 14,813/14,813 facts have valid_at + recorded_at |
| 3.4 | Epistemic states | ✅ | active 12,507 · superseded 2,244 · contradicted 62; 3,977 SUPERSEDES + 32 CONTRADICTS edges |
| 7.2 | Extraction (GLM-5.2) | ✅ | compose env `LLM_MODEL=glm-5.2`, live pipeline |
| 7.7 | File storage | ✅ | 1,031 episodes → 0 missing files; 14,813 facts → 0 broken chains; `_index.json` 2,345 = disk count |
| 4.1 | MCP transport | ✅ | stdio + streamable HTTP `/mcp/` |
| 4.2 | MCP tools | ✅ | 11 tools (spec 7 + inject/stats/tempr/consolidate/mental_model) |
| 6.1–6.3 | Keys, scopes, namespaces | ✅ | SQLite keys.db; 403 on ns mismatch; admin scope gate |
| 6.4 | Read-your-writes | ✅ | LRU pending-ingestion merge in /search |
| 4.1 | Rate limiting | ✅ | Redis sliding window: 100 reads/10s, 20 writes/10s, 429 + Retry-After header |
| 8.1 | Circuit breaker | ✅ | CLOSED/OPEN/HALF_OPEN; `/circuit-breaker` endpoint |
| 4.2 | Provenance chain | ✅ | `ferrite_get_provenance` returns agent→channel→session→episode→file |
| 8.2 | Health checks | ✅ | `/health` returns neo4j/redis/queue_depth |
| 10.1 | Backup script | ✅ | `scripts/backup.sh` (A12): stop→BGSAVE→dump→tar→30d retention |
| A5 | Eval harness | ✅ | 33 queries, pass/fail + missing-substring grading |

**Caveat on file storage:** verified for `text/` (session transcripts).
Spec §7.7 envisions papers/web/code/voice/uploads subdirs — `content_type`
routing exists in code but has never carried anything but session
transcripts. Live ingest of other types is untested.

**Caveat on eval:** 33 queries self-graded by substring matching; recall@5
cutoff per query is present in `eval.py` but the §1.1 kill criteria
(recall@5 < 0.6 → stop) is not wired to anything automatic.

---

## Deviations from spec

1. **Extraction model (§7.2/§11.1):** spec says DS-V4-Flash; running
   GLM-5.2. GLM-5.2 via LiteLLM measured ~50s per 9KB episode, quality
   fine at current volume. **Decision needed:** ratify in spec or switch.
2. **Hermes plugin location (§5.2):** spec says
   `~/.hermes/plugins/ferrite_plugin/`; current attempt was a bundled
   `plugins/memory/ferrite/` dir (empty, inert). Plugin still needs
   building — see below.

---

## What's missing vs spec (all deferred/post-MVP, per §1.1)

- Web UI beyond Neo4j Browser (§9) — FastAPI static SPA exists, not the
  spec's Next.js Graph Explorer; acceptable interim.
- Auto-inject (§4.4/A17) — MCP `ferrite_inject` tool exists; per-agent
  hook wiring is the missing half, arrives with plugins.
- Observation consolidation / mental models / TEMPR — implemented in code
  with tests, but zero real data has flowed through them (no observations
  beyond migration data).
- Markdown mirror (§10.3), namespaces beyond shared/personal.
- Agent plugins other than Hermes (§5.1 matrix): Claude Code, Codex,
  OMP, Pi — all stubs.

## Operational debt (not spec violations)

- 1,321 orphan files in `data/files/text/` from the Aug 10 reset —
  episodes deleted, files survived. Harmless; dedupe pass recommended.
- Backfill runs as a LaunchAgent writing directly to Neo4j, bypassing
  the API's LLM queue — fine for one-shot migration, but the incremental
  ingester must go through the API (`/store`), not raw Bolt writes.
- Dead-lettered episodes from LLM timeouts during backfill: check
  `data/dlq/` after backfill completes, re-run if small.
- CI (`scripts/ci_pipeline.py`) last green 2026-08-10; re-run before
  any release.

## Recommended order of operations

1. Finish backfill (running, ETA hours) → re-run eval, record baseline.
2. Build incremental ingester via API `/store` (launchd timer).
3. Cut plugin repos: hermes, claude-code, codex, omp, pi — each
   standalone repo, each shipping skills, released together.
4. Post-release: wire eval kill-criteria, dedupe orphans, then start
   closing deferred §-items (auto-inject, consolidation flows).
