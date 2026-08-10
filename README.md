<div align="center">

# 🧲 Ferrite

### Temporal Knowledge Graph Memory for AI Agents

**The magnetic compound on cassette tape that holds the recording.**
**Without ferrite, the tape is just plastic film. Ferrite is what holds the signal.**

<br>

<img src="https://img.shields.io/badge/tests-214%20passing-brightgreen?style=flat-square" alt="Tests">
<img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License">
<img src="https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
<img src="https://img.shields.io/badge/Neo4j-5.x-0084D3?style=flat-square&logo=neo4j&logoColor=white" alt="Neo4j">
<img src="https://img.shields.io/badge/Redis-8-DC382D?style=flat-square&logo=redis&logoColor=white" alt="Redis">
<img src="https://img.shields.io/badge/FastAPI-async-009639?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">

<br>

*Part of the [Kassett](https://github.com/fattchris/kassett) ecosystem — the cassette shell that holds the tape.*

</div>

---

## 📖 What is Ferrite?

Ferrite is a **temporal knowledge graph** that gives AI agents long-term memory.

Every fact is stored with:
- **Full provenance** — which agent, which session, when
- **Temporal tracking** — when it was true, when it changed
- **Bitemporal queries** — *"what did we know in July?"* vs *"what was true in July?"*
- **Multi-strategy retrieval** — 5 parallel search strategies fused via RRF
- **Observation consolidation** — raw facts → synthesized beliefs with evidence tracking
- **Mental models** — persona-specific knowledge curation

Agents store facts via MCP or REST. Ferrite extracts entities and relationships using an LLM, canonicalizes entities, embeds fact statements, and indexes everything for sub-second retrieval.

```
┌─────────────────────────────────────────────────────────────────┐
│                          AGENTS                                  │
│   Hermes  ·  Claude  ·  Codex  ·  OpenClaw  ·  Any MCP client   │
└──────┬──────────┬──────────┬──────────┬──────────┬─────────────┘
       │          │          │          │          │
       ▼          ▼          ▼          ▼          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MCP SERVER (10 tools)                         │
│   search · query · entity_facts · multi_hop · inject            │
│   stats · ingest · tempr_search · mental_model · consolidate    │
└───────────────────────┬─────────────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
  │  REST API    │ │ INGESTION    │ │  WEB UI      │
  │  (FastAPI)   │ │ PIPELINE     │ │  (SPA)       │
  │              │ │              │ │              │
  │ /search      │ │ Redis Queue  │ │ Search       │
  │ /store       │ │ DLQ          │ │ TEMPR panel  │
  │ /tempr       │ │ Circuit      │ │ Stats        │
  │ /entities    │ │ Breaker      │ │ Wiki         │
  │ /eval        │ │              │ │              │
  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
         │                │                │
         ▼                ▼                ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                     Neo4j 5.x                                   │
  │  Reified facts · Bitemporal edges · Vector index (768d)         │
  │  + Redis 8 (cache + queue + AOF persistence)                    │
  └─────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Option 1: CLI Installer *(recommended)*

```bash
git clone https://github.com/fattchris/ferrite.git
cd ferrite
bash scripts/install.sh
```

The installer walks you through:
1. Prerequisite checks (Docker, Python, uv)
2. Secure secret generation (.env)
3. LLM extraction backend config
4. Optional Ollama for local embeddings
5. Docker stack build + start
6. Optional TLS via Caddy (auto-restart)
7. Optional Hermes memory provider plugin
8. Deployment verification

### Option 2: Web GUI Installer

```bash
git clone https://github.com/fattchris/ferrite.git
cd ferrite
docker compose -f docker-compose.prod.yml up -d --build
```

Then open the web installer:

```
http://localhost:8001/install
```

A 5-step wizard with secret generation, LLM config, .env preview, and live deployment verification.

### Option 3: Manual

```bash
# Clone
git clone https://github.com/fattchris/ferrite.git
cd ferrite

# Generate secrets
cp .env.example .env
# Edit .env — generate secrets with: openssl rand -hex 32

# Build and start
docker compose -f docker-compose.prod.yml up -d --build

# Verify
curl http://localhost:8001/health
```

---

## 💾 Where Data is Stored

Ferrite persists data across several Docker volumes and host paths. Understanding where your data lives is critical for backups, migrations, and debugging.

### Data Storage Map

```
┌──────────────────────────────────────────────────────────────────┐
│                        HOST MACHINE                              │
│                                                                  │
│  ~/ferrite/                                                      │
│  ├── .env                    ← Config (secrets, LLM, limits)     │
│  ├── backups/                ← Nightly dumps (cron 3AM, 30d)    │
│  │   ├── dump-YYYYMMDD/      ←   Neo4j full dump                │
│  │   └── ferrite-YYYYMMDD-volumes.tar.gz ← Redis + API volumes  │
│  └── docker-compose.prod.yml                                    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
         │  docker compose up
         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    DOCKER VOLUMES                                │
│                                                                  │
│  ferrite_neo4j_data          → /data (in neo4j container)        │
│    /var/lib/docker/volumes/ferrite_neo4j_data/_data              │
│    Graph: reified facts, edges, vector index                     │
│                                                                  │
│  ferrite_redis_data          → /data (in redis container)        │
│    /var/lib/docker/volumes/ferrite_redis_data/_data              │
│    Cache + queue + AOF persistence (fsync every 1s)             │
│                                                                  │
│  ferrite_ferrite_api_data    → /app/data (in API container)      │
│    /var/lib/docker/volumes/ferrite_ferrite_api_data/_data        │
│    SQLite key store, API data files                              │
│                                                                  │
│  ferrite_prometheus_data     → /prometheus                       │
│    /var/lib/docker/volumes/ferrite_prometheus_data/_data         │
│    Metrics time-series (15s scrape interval)                     │
│                                                                  │
│  ferrite_caddy_data          → /data (in caddy container)        │
│    /var/lib/docker/volumes/ferrite_caddy_data/_data              │
│    TLS certificates (Let's Encrypt / self-signed)               │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Volume Reference

| Volume | Container Path | Host Mountpoint | Contents |
|--------|---------------|-----------------|----------|
| `ferrite_neo4j_data` | `/data` | `/var/lib/docker/volumes/ferrite_neo4j_data/_data` | Neo4j graph database — all facts, entities, relationships, vector index |
| `ferrite_redis_data` | `/data` | `/var/lib/docker/volumes/ferrite_redis_data/_data` | Redis cache + ingestion queue, AOF-persisted (fsync every 1s) |
| `ferrite_ferrite_api_data` | `/app/data` | `/var/lib/docker/volumes/ferrite_ferrite_api_data/_data` | SQLite API key store, API data files |
| `ferrite_prometheus_data` | `/prometheus` | `/var/lib/docker/volumes/ferrite_prometheus_data/_data` | Prometheus metrics time-series |
| `ferrite_caddy_data` | `/data` | `/var/lib/docker/volumes/ferrite_caddy_data/_data` | Caddy TLS certificates + state |

### Host Paths

| Path | Contents |
|------|----------|
| `~/ferrite/.env` | Configuration — secrets, LLM backend, rate limits |
| `~/ferrite/backups/` | Nightly backups — Neo4j dump + volume tarball, 30-day retention |

### Backup Schedule

- **Schedule**: Nightly cron at 3:00 AM
- **Contents**: Neo4j full database dump + Redis volume + API data volume
- **Retention**: 30 days (older backups pruned automatically)
- **Script**: `scripts/backup.sh` (stops writers, dumps, copies volumes)
- **Restore**: `scripts/restore.sh YYYYMMDD`

---

## 🖥️ Web UI

Ferrite ships with a built-in web interface — no separate frontend to deploy.

**URL**: `http://localhost:8001/`

The Web UI is a single-page app served directly by the FastAPI backend. It provides three primary tabs:

### Search Tab

BM25 fulltext search with optional hybrid mode. Enter a query and get ranked fact statements with certainty scores, provenance, and temporal metadata.

```
http://localhost:8001/
```

- BM25 fulltext search on fact statements
- Results show statement, certainty %, and source
- Hit Enter or click Search to query

### TEMPR Tab

Multi-strategy retrieval interface. Enter a natural-language query and TEMPR runs 5 parallel search strategies fused via Reciprocal Rank Fusion (k=60):

1. **Semantic** — Vector cosine similarity (768d)
2. **BM25** — Fulltext keyword match
3. **Graph** — Entity neighborhood traversal
4. **Temporal** — Time-weighted scoring
5. **Recency** — Freshness boost

Each strategy degrades gracefully — if Ollama is down, semantic falls back to BM25-only. **Retrieval never fails.**

### Stats Tab

Live system dashboard with health metrics and an entity browser:

- **Overall Health** — aggregated status (healthy / degraded)
- **Neo4j** — connection status
- **Redis** — connection status
- **Queue Depth** — ingestion queue backlog
- **Circuit Breaker** — state machine status (CLOSED / OPEN / HALF_OPEN)
- **Failure Count** — current failure tally
- **Entity Browser** — click any entity card to drill into its full fact graph (facts as subject + facts as object)

### API Key Bar

All protected endpoints require a Bearer token. The key bar at the top of the page lets you paste your `FERRITE_API_KEY` — it's stored in localStorage and sent as `Authorization: Bearer <token>` on every request.

---

## 📚 Wiki / Knowledge Explorer

Ferrite includes an **Obsidian-like knowledge browser** accessible at:

```
http://localhost:8001/wiki
```

The Wiki view is a full-screen, three-pane interface for navigating your temporal knowledge graph as a human-readable knowledge base:

### Sidebar — Entity Browser

- Searchable list of all entities in the graph
- Type-aware icons (👤 person, 🖥️ server, 🧠 model, 🏢 org, 📦 software, 📍 location, 💡 concept, ⚙️ config, 📄 file, 🌐 network, 🔌 service)
- Live entity count and operation stats in the footer

### Main Pane — Entity Detail

Selecting an entity opens a rich detail view with four tabs:

| Tab | What it shows |
|-----|---------------|
| **📋 Facts** | All facts where this entity is the subject or object, with certainty %, predicate, assertion source, valid-at and recorded-at timestamps, and epistemic state (active / contradicted / superseded) |
| **🌐 Graph** | Interactive radial SVG graph — center node is the selected entity, surrounding nodes are connected entities, edges labeled with predicates. Click any node to navigate. |
| **📅 Timeline** | Chronological view of all facts sorted by valid-at date — trace how knowledge about this entity evolved over time |
| **🔗 Backlinks** | All entities that reference this one — click any backlink to traverse to that entity's page |

### Navigation

- **Breadcrumb** — Wiki → Entity Type → Entity Name
- **Fact links** — object entities in fact statements are clickable; literals show as inline code
- **Graph traversal** — click any node in the radial graph to load that entity
- **API key** — same Bearer token bar as the main Web UI, stored in localStorage

### Top Bar

- 🧲 Ferrite logo + **Wiki** badge
- Navigation links: Search · Wiki · Install
- Live health indicators: Neo4j status dot, Redis status dot, queue depth

---

## 🏗️ Architecture

### Knowledge Model

Facts are **reified nodes**, not edges. This means:

- Every fact has its own identity (UUID)
- Facts can be superseded, contradicted, or supported
- Entities are global referents — they carry no epistemic state
- All belief semantics live on Fact nodes

```
Entity ←—SUBJECT—— Fact ——OBJECT——→ Entity/Literal
                       │
                  SOURCED_FROM
                       │
                    Episode
                       │
                     Session
```

### Fact Node

```python
Fact:
    id: str                 # UUID
    predicate: str          # Controlled vocab (70 predicates)
    statement: str          # Canonical rendered sentence
    functional: bool        # Derived from vocab at write time
    certainty: enum         # stated | inferred | speculative
    epistemic_state: enum   # active | contradicted | superseded
    assertion_source: enum  # user | tool_result | model
    valid_at: datetime      # When the fact became true
    recorded_at: datetime   # When we learned it
    invalid_at: datetime    # When it stopped being true
    namespace: str          # shared | personal | ...
```

### TEMPR — 5-Strategy Retrieval

TEMPR (Temporal Multi-Party Retrieval) runs 5 search strategies in parallel and fuses results via Reciprocal Rank Fusion (k=60):

1. **Semantic** — Vector cosine similarity (768d, nomic-embed-text)
2. **BM25** — Fulltext search on fact statements
3. **Graph** — Entity neighborhood traversal
4. **Temporal** — Time-weighted scoring
5. **Recency** — Freshness boost

Priority order: Mental Models → Observations → Raw Facts.

Each strategy degrades gracefully. If Ollama is down, semantic search falls back to BM25-only. If Neo4j graph traversal is slow, it skips to the other strategies. **Retrieval never fails.**

### Circuit Breaker

```
CLOSED → (5 failures) → OPEN → (60s cooldown) → HALF_OPEN → success → CLOSED
                                                      → failure → OPEN
```

When OPEN: all MCP and API calls return immediately with a fallback response. Agents degrade to local-only memory. No crash, no hang. Manual reset via `POST /circuit-breaker/reset`.

### Observation Consolidation

Raw facts accumulate over time. The consolidator groups observations by `(entity, predicate, namespace)`, detects contradictions, and creates synthesized Observation nodes with evidence tracking:

```
Fact A: "Spark-01 runs GLM-5.2"     ──SUPPORTS──→ Observation
Fact B: "Spark-01 runs GLM-5.2"     ──SUPPORTS──→ (same)
Fact C: "Spark-01 runs DeepSeek"   ──CONTRADICTS──→ (same)
```

Observations carry `proof_count` and `evidence_refs`. Stale observations are flagged for re-verification.

### Bitemporal Model

Two query modes:

- **`as_of_knowledge(T)`** — What did we know at time T? (transaction time)
- **`as_of_world(T)`** — What was true at time T? (valid time)

Facts are never deleted. Old facts get `invalid_at` + `SUPERSEDES` edge linking to the replacement.

---

## 🔌 API Reference

### Endpoints

| Service | URL | Auth |
|---------|-----|------|
| REST API | `http://localhost:8001` | Bearer token |
| Web UI | `http://localhost:8001/` | Public |
| Wiki / Knowledge Explorer | `http://localhost:8001/wiki` | Public (queries need API key) |
| Web Installer | `http://localhost:8001/install` | Public |
| Neo4j Browser | `http://localhost:7474` | neo4j / (from .env) |
| Prometheus | `http://localhost:9090` | Public |
| TLS (if Caddy) | `https://localhost:9443` | Self-signed |

### Public Endpoints

```
GET  /                           Web UI (SPA)
GET  /wiki                       Knowledge Explorer (Obsidian-like wiki)
GET  /install                    Web GUI Installer
GET  /health                     Neo4j + Redis + queue status
GET  /metrics                    JSON health + metrics
GET  /metrics/prometheus         Prometheus text format
GET  /circuit-breaker            Circuit breaker state
POST /circuit-breaker/reset     Reset circuit breaker
```

### Protected Endpoints (Bearer Token)

```
GET  /search?query=...&limit=10         BM25 fulltext search
POST /store                             Queue content for ingestion
GET  /entities/{id}                     Entity with all facts
GET  /history/{id}?mode=knowledge       Temporal history (knowledge|world)
POST /tempr                             5-strategy RRF retrieval
GET  /mental-models?query=...           Mental model search
POST /consolidate                        Run observation consolidation
GET  /eval                               Run eval harness (30 queries)
```

### API Key Management

```
POST /keys                            Create new API key
GET  /keys                            List all API keys
POST /keys/{key_id}/revoke            Revoke an API key
```

---

## 🔧 MCP Tools (10)

Ferrite exposes 10 MCP tools for agent integration:

| Tool | Description |
|------|-------------|
| `ferrite_search` | Search facts by keywords |
| `ferrite_query` | Natural language → Cypher |
| `ferrite_entity_facts` | Get all facts for an entity |
| `ferrite_multi_hop` | Multi-hop graph traversal |
| `ferrite_inject` | Auto-inject context (TEMPR + score floor) |
| `ferrite_stats` | Graph statistics |
| `ferrite_ingest` | Ingest content via LLM extraction |
| `ferrite_tempr_search` | TEMPR 5-strategy retrieval |
| `ferrite_mental_model` | Mental model search |
| `ferrite_consolidate` | Run observation consolidation |

### Agent Integration

#### Hermes (native plugin)

```bash
hermes memory setup    # select "ferrite"
hermes memory status   # verify active
```

Tools injected: `ferrite_search`, `ferrite_add`, `ferrite_entity`, `ferrite_multi_hop`, `ferrite_stats`.

Lifecycle hooks: `prefetch` (before LLM call), `sync_turn` (after each turn), `on_session_end` (final flush), `on_memory_write` (mirror MEMORY.md writes), `on_pre_compress` (save before context compaction).

Circuit breaker: 5 failures → 120s cooldown → graceful degradation to local-only memory.

#### Claude Code / Cursor / VS Code (MCP)

Ferrite is an [Agent Plugins 1.0.0](https://agent-plugins.org) compliant plugin. Add to your MCP config:

```json
{
  "mcpServers": {
    "ferrite": {
      "command": "python",
      "args": ["-m", "ferrite.mcp_server"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_PASSWORD": "your-password",
        "FERRITE_API_KEY": "your-api-key"
      }
    }
  }
}
```

#### Any MCP-compatible agent

Use the MCP server via stdio:

```bash
python -m ferrite.mcp_server
```

Or HTTP transport at `/mcp/`:

```
POST http://localhost:8001/mcp/
```

---

## ⚙️ Configuration

All config via environment variables (`.env` file in project root):

```bash
# Required
NEO4J_PASSWORD=<openssl rand -hex 32>
FERRITE_API_KEY=<openssl rand -hex 32>

# LLM extraction backend
LLM_BASE_URL=http://localhost:4000/v1    # LiteLLM, OpenRouter, etc.
LLM_API_KEY=sk-...                        # Blank for Ollama
LLM_MODEL=gpt-4o-mini

# Optional
FERRITE_DOMAIN=localhost                  # Real domain for Let's Encrypt
NEO4J_URI=bolt://localhost:7687
REDIS_URL=redis://localhost:6379
WRITE_RATE_LIMIT=100                      # Per minute
READ_RATE_LIMIT=200                       # Per minute
```

---

## 🧪 Testing

```bash
# 176 unit tests + 12 module tests (fast, no Docker needed)
uv run pytest tests/ -q

# 38 E2E tests (requires running Docker stack)
FERRITE_API_KEY=$(grep FERRITE_API_KEY .env | cut -d= -f2) \
NEO4J_PASSWORD=$(grep NEO4J_PASSWORD .env | cut -d= -f2) \
uv run python scripts/e2e_test.py

# Load test (100 RPS)
uv run python scripts/load_test.py \
  --base-url http://localhost:8001 \
  --api-key $(grep FERRITE_API_KEY .env | cut -d= -f2)

# Lint
uv run ruff check src/ tests/ scripts/
```

### Test Results

| Suite | Count | Status |
|-------|-------|--------|
| Unit tests | 176 + 12 | ✅ Passing |
| E2E tests | 38 | ✅ Passing |
| Load test | 100 RPS | ✅ p95 < 20ms, 0 errors |

### CI Pipeline

```yaml
jobs:
  lint:     # ruff check — zero warnings
  test:     # 176 unit tests (Neo4j + Redis services)
  e2e:      # 38 E2E tests against live API
  eval:     # Recall@5 ≥ 0.30 regression gate
```

---

## 🗄️ Backup & Recovery

```bash
# Backup (stops writers, dumps Neo4j, copies volumes)
bash scripts/backup.sh

# Restore from a specific date
bash scripts/restore.sh YYYYMMDD

# Health check
bash scripts/health_check.sh
```

Backups are written to `~/ferrite/backups/` and include:
- Neo4j full database dump (`dump-YYYYMMDD/`)
- Redis volume data
- API data volume data
- Combined tarball (`ferrite-YYYYMMDD-volumes.tar.gz`)

**Schedule**: Nightly cron at 3:00 AM · **Retention**: 30 days

---

## 📁 Project Structure

```
ferrite/
├── src/ferrite/
│   ├── api.py              # FastAPI REST + Web UI + Installer + Wiki
│   ├── mcp_server.py       # MCP server (10 tools)
│   ├── ingestion.py        # Queue → extract → canonicalize → Neo4j
│   ├── query.py            # Search, NL→Cypher, context injection
│   ├── tempr.py            # 5-strategy RRF retrieval
│   ├── consolidator.py     # Observation synthesis
│   ├── mental_models.py    # Persona archetypes
│   ├── circuit_breaker.py  # CLOSED/OPEN/HALF_OPEN state machine
│   ├── embeddings.py       # Ollama 768d + VectorStore ABC
│   ├── temporal.py         # as_of_knowledge / as_of_world
│   ├── schema.py           # Neo4j constraints + indexes + vector
│   ├── canonicalize.py     # Entity normalization + alias resolution
│   ├── extractor.py        # LLM extraction prompt + validation
│   ├── metrics.py          # MetricsCollector
│   ├── observability.py    # HealthMonitor + AlertManager
│   ├── eval.py             # Recall@K + MRR harness
│   ├── config.py           # Pydantic settings
│   ├── models.py           # Pydantic models (Fact, Entity, Episode)
│   ├── vocab.py            # 70 controlled predicates
│   ├── key_store.py        # SQLite API key management
│   └── static/
│       ├── index.html      # Web UI (SPA — Search, TEMPR, Stats)
│       ├── wiki.html       # Knowledge Explorer (entity browser, graph, timeline)
│       └── install.html    # Web GUI Installer
├── tests/                  # 176 + 12 unit tests
├── scripts/
│   ├── install.sh          # CLI Installer
│   ├── e2e_test.py         # 38 E2E tests
│   ├── load_test.py        # Load/performance tests
│   ├── backup.sh           # Neo4j + volume backup
│   ├── restore.sh          # Restore from backup
│   ├── health_check.sh    # Service health check
│   ├── migrate_from_sqlite.py  # Hermes session → graph migration
│   ├── seed.py             # Seed sample data
│   ├── eval.py             # Eval harness runner
│   └── audit_build.py      # Spec compliance audit
├── eval/queries.yaml       # 30-query eval dataset
├── docker-compose.yml      # Dev stack
├── docker-compose.prod.yml # Production stack
├── Dockerfile              # Python 3.12-slim + uv
├── Caddyfile               # TLS config
├── prometheus.yml          # Metrics scraping
├── plugin.json             # Agent Plugins 1.0.0 manifest
├── mcp.json                # MCP server config
└── .github/workflows/ci.yml  # CI pipeline
```

---

## 🖼️ Screenshots

> **TODO**: Add screenshots of the Web UI and Wiki views here.
>
> - Web UI — Search tab with results
> - Web UI — TEMPR tab with multi-strategy results
> - Web UI — Stats tab with entity browser
> - Wiki — Entity detail with fact list
> - Wiki — Radial graph view
> - Wiki — Timeline view
>
> Place screenshots in `docs/screenshots/` and reference them here:
>
> ```markdown
> ![Search Tab](docs/screenshots/search.png)
> ![Wiki Entity Detail](docs/screenshots/wiki-entity.png)
> ![Wiki Graph View](docs/screenshots/wiki-graph.png)
> ```

---

## 🛠️ Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Graph DB | Neo4j 5 Community | Purpose-built for graph traversal, free, Docker |
| Cache/Queue | Redis 8 | AOF persistence, pub/sub, rate limiting |
| API | FastAPI | Async, OpenAPI docs, WebSocket support |
| MCP | FastMCP | Standard protocol for agent ↔ tool communication |
| Embeddings | nomic-embed-text (768d) | 8192 context, multilingual, via Ollama |
| Extraction | Config-driven (LiteLLM/OpenRouter/Ollama) | Any LLM, no vendor lock-in |
| TLS | Caddy 2 | Auto-HTTPS, HTTP/3, zero-config certs |
| Metrics | Prometheus | Industry standard, text exposition format |
| CI | GitHub Actions | Lint + test + E2E + eval regression gate |

---

## ⚡ Performance

On a Mac Mini M4 with the prod stack:

```
Search:      100 RPS, p50=14ms, p95=18ms, 0 errors
Ingestion:   10 RPS, p50=4ms (queue is async)
Rate limit:  429 enforced at configured threshold
```

### Eval Results

30-query test suite across 5 query classes:

```
entity_lookup:   10/10
temporal:         5/5
multi_hop:        5/5
paraphrase:       5/5
inject:           5/5
```

Metrics: Recall@5, Recall@10, MRR.

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

## Etymology

> Named after ferric oxide (Fe₂O₃), the magnetic compound on cassette tape
> that physically holds the recording. Without ferrite, the tape is just
> plastic film. Ferrite is what holds the signal.
>
> Parent brand: **Kassett** — the cassette shell that holds the tape.

---

<div align="center">

**[Install](scripts/install.sh) · [Web UI](http://localhost:8001) · [Wiki](http://localhost:8001/wiki) · [Docs](https://github.com/fattchris/ferrite) · [Kassett](https://github.com/fattchris/kassett)**

*Ferrite is what holds the signal.*

</div>
