# Ferrite — Growth Forecast & Bottleneck Analysis

> Hardware: Mac Mini M2 24GB RAM
> Docker budget: ~6-8GB (Neo4j 3GB, Redis 256MB, API 256MB, worker 256MB, UI 256MB, Ollama shared)
> Storage: 512GB SSD

## Assumptions

- Average session: 50 messages, 500 words/message = 25,000 words/session
- Entities per session: ~15 (people, projects, tools, concepts)
- Relationships per session: ~25 (X deployed Y, Y uses Z, etc.)
- Episodes per session: 1 (the session itself is one episode)
- Embeddings: 1 per entity + 1 per episode = ~16/session
- Extraction LLM tokens per session: ~5,000 input (session text) + ~1,000 output (triples)
- Ingestion rate: 15/day initially → 50/day at 6mo → 100/day at 2yr

## Data Growth

### Nodes

| Per session | ~16 nodes (15 entities + 1 episode) |
| Node size on disk | ~150 bytes |

| Period | Sessions | Nodes | Node disk |
|--------|----------|-------|-----------|
| 2 months | 900 | 14,400 | 2.1 MB |
| 6 months | 9,000 | 144,000 | 21 MB |
| 2 years | 73,000 | 1,168,000 | 168 MB |

### Edges

| Per session | ~25 edges |
| Edge size on disk | ~70 bytes |

| Period | Sessions | Edges | Edge disk |
|--------|----------|-------|-----------|
| 2 months | 900 | 22,500 | 1.6 MB |
| 6 months | 9,000 | 225,000 | 16 MB |
| 2 years | 73,000 | 1,825,000 | 128 MB |

### Total Neo4j graph on disk

| Period | Graph disk (nodes+edges+overhead) |
|--------|----------------------------------|
| 2 months | ~5 MB |
| 6 months | ~50 MB |
| 2 years | ~350 MB |

**Neo4j graph data is NOT the bottleneck.** 350MB after 2 years fits easily in the 1.5GB page cache. All queries stay hot.

## Vector Growth

| Per session | ~16 embeddings (768d × 4 bytes = 3KB each) |
| Vector store per session | ~48 KB |

| Period | Sessions | Embeddings | Vector disk |
|--------|----------|------------|-------------|
| 2 months | 900 | 14,400 | 43 MB |
| 6 months | 9,000 | 144,000 | 432 MB |
| 2 years | 73,000 | 1,168,000 | 3.5 GB |

**Vector store IS the first scaling concern.** At 2 years, 3.5GB of vectors exceeds the Neo4j page cache (1.5GB). Vector similarity search will hit disk. Neo4j's vector index is less optimized than dedicated vector DBs (Qdrant, Milvus).

**Mitigation:** Switch to an external vector store (Qdrant) when embeddings exceed 500K (~1.5GB). This is a config change, not an architecture change — the extraction worker already writes to "data stores" plurally.

## Redis Queue

| Extraction time | ~5-10s per session (DS-V4-Flash) |
| Daily ingestion at 100/day | 8-16 min total |
| Backfill 1,000 sessions | 83-166 min (1.5-3 hrs) |
| Redis memory for 1K queued items | ~50 MB (JSON payloads) |

**Redis is never a bottleneck.** Even a massive 10,000-session backfill fits in ~500MB Redis memory and processes in 14-28 hours (overnight job).

## Ollama Memory (Embedding Model)

- nomic-embed-text model size: ~137 MB on disk
- RAM when loaded: ~300-500 MB (model weights + inference overhead)
- Ollama `keep_alive` default: 5 min idle → unloads
- Config: set `keep_alive: -1` to keep permanently resident

**Impact on 24GB Mini:**
| Component | RAM |
|-----------|-----|
| macOS + system | ~4 GB |
| LiteLLM proxy | ~200 MB |
| Home Assistant | ~300 MB |
| Docker (Neo4j + Redis + API + Worker + UI) | ~5 GB |
| Ollama (nomic-embed-text) | ~500 MB |
| Hermes gateway | ~500 MB |
| **Total** | **~10.5 GB** |
| **Headroom** | **~13.5 GB** |

**Memory is NOT a bottleneck.** 13.5GB headroom. Even with aggressive ingestion, the Mini has room. The prior crash was from Neo4j OOM (fixed by 3GB cap), not total system memory.

## LLM Extraction Cost

DS-V4-Flash runs on Spark07-08 (our hardware). No per-token API cost — it's self-hosted. The DeepSeek API pricing ($0.14/1M input, $0.28/1M output) is irrelevant since we use local inference via LiteLLM.

**Real cost:** GPU time on Spark07-08. DS-V4-Flash is TP2 (2 GPUs). Extraction is lightweight — 5,000 input tokens + 1,000 output tokens per session. At 100 sessions/day:

| Metric | Value |
|--------|-------|
| Tokens/day (100 sessions) | 600K (500K in + 100K out) |
| GPU seconds per extraction | ~5-10s |
| Total GPU time/day | 8-16 min |
| Spark utilization impact | <2% of daily capacity |
| When to consider CPU extraction | Never (Spark has capacity, local is free) |

**LLM cost is NOT a bottleneck.** Self-hosted on Spark, negligible utilization impact.

## Disk Growth (Total)

| Period | Neo4j | Vectors | Markdown mirror | Redis | Total |
|--------|-------|---------|-----------------|-------|-------|
| 2 months | 5 MB | 43 MB | 10 MB | 50 MB | ~110 MB |
| 6 months | 50 MB | 432 MB | 100 MB | 50 MB | ~630 MB |
| 2 years | 350 MB | 3.5 GB | 800 MB | 100 MB | ~4.8 GB |

**Disk is NOT a bottleneck.** 512GB SSD. 2 years of data = <5GB.

## Bottleneck Ranking (by when they hit)

### 1. Vector search latency (hits at ~1.5 years)
- When embeddings exceed ~500K (1.5GB), Neo4j vector index exceeds page cache
- Vector similarity queries hit disk → 100-500ms latency
- **Fix:** Move vectors to Qdrant (dedicated vector DB, ~100MB RAM, disk-optimized)
- **Timeline:** ~12-18 months at 100 sessions/day

### 2. Markdown mirror bloat (hits at ~1 year)
- 800MB of markdown files after 2 years. Greppable but slow to index.
- **Fix:** Full-text search via Neo4j or SQLite FTS5 instead of grep
- **Timeline:** ~12 months

### 3. Backfill ingestion time (hits immediately on first deploy)
- Backfilling 1000+ existing sessions takes 1.5-3 hours
- Not a runtime bottleneck, but a one-time operational concern
- **Fix:** Parallelize extraction worker (2-3 concurrent workers)
- **Timeline:** Day 1 (backfill), then resolved

### 4. Extraction LLM latency (minor, ongoing)
- 5-10s per session extraction. Sessions queue up during bulk ingestion.
- At 100/day, 8-16 min total — not a problem.
- At 1000/day (bulk backfill), 1.5-3 hrs — overnight job.
- **Fix:** Parallel extraction workers (Redis supports concurrent consumers)
- **Timeline:** Only matters during backfill, not steady-state

### 5. Neo4j query complexity (hits at ~2+ years)
- With 1M+ nodes and 1.8M edges, complex multi-hop queries (3+ hops) slow down
- Page cache handles hot data, but cold traversals hit disk
- **Fix:** Query optimization, add composite indexes, limit traversal depth
- **Timeline:** 2+ years, and still manageable

## Summary: What Grows Unmanageable?

**Nothing breaks in 2 months.** The system is comfortable.

**At 6 months:** Still fine. ~150MB graph, ~432MB vectors. All in page cache.

**At 1 year:** Vectors approaching page cache limit (~1GB). Markdown mirror getting large. Consider external vector store migration.

**At 2 years:** Vector store (3.5GB) is the primary scaling concern. Graph itself (350MB) is fine. Disk (4.8GB total) is fine. Memory (10.5GB used) is fine. The system needs a vector store migration, not a re-architecture.

**The bottleneck is vector storage, not the graph.** Plan for Qdrant migration at ~12-18 months. Everything else scales gracefully.

## Recommendations

1. **Design for pluggable vector store from day 1.** The extraction worker writes embeddings to a "vector_store" interface. MVP uses Neo4j's built-in vector index. Config can swap to Qdrant later without touching the extraction pipeline.

2. **Don't over-engineer the graph.** 350MB after 2 years is trivial for Neo4j. Focus engineering on extraction quality, not graph scaling.

3. **Backfill strategy:** Run initial backfill overnight. 1000 sessions × 10s = ~3 hours. Use 2 parallel workers to halve it.

4. **Monitoring:** Track vector count and vector store size. Alert at 500K embeddings (trigger point for Qdrant migration planning).

5. **Ollama config:** Set `keep_alive: -1` for nomic-embed-text to keep it always loaded. 500MB RAM cost is worth the latency savings.
