# Ferrite — Temporal Knowledge Graph System

A temporal knowledge graph memory system with multi-strategy retrieval (TEMPR), observation consolidation, and mental model support. Built on Neo4j 5, Redis 8, and Ollama embeddings.

## Quick Start

```bash
# Clone and install
git clone <repo-url> ferrite
cd ferrite
uv sync

# Start services via Docker
docker compose up -d

# Set API key (optional — disabled by default)
export FERRITE_API_KEY=your-secret-key

# Run tests
uv run pytest
uv run python scripts/e2e_test.py

# Open the Web UI
open http://localhost:8001
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Ferrite Stack                   │
├─────────────────────────────────────────────────┤
│  Web UI (:8001)     REST API (:8001)             │
│  - Search            - /health (public)          │
│  - TEMPR             - /search (auth)           │
│  - Stats dashboard   - /tempr (auth)             │
│                      - /entities/{id} (auth)     │
│                      - /store (auth)             │
│                      - /eval (auth)              │
│                      - /circuit-breaker (public) │
├─────────────────────────────────────────────────┤
│  MCP Server (stdio)  10 tools                    │
├─────────────────────────────────────────────────┤
│  TEMPR Engine    │ Consolidator │ Circuit Breaker │
│  5 strategies    │ Redis queue  │ CLOSED/OPEN/  │
│  RRF k=60        │ SUPPORTS/   │ HALF_OPEN      │
│                  │ CONTRADICTS  │                │
├─────────────────────────────────────────────────┤
│  Neo4j 5 (:7687)  │  Redis 8 (:6379)  │  Ollama  │
│  Graph + Vector   │  Cache + Queue   │  768d emb │
└─────────────────────────────────────────────────┘
```

## Components

### Core
- **Neo4j 5** — Graph database with fulltext + vector indexes
- **Redis 8** — Cache, job queue, rate limiting
- **Ollama** — `nomic-embed-text` 768d embeddings (degrades to BM25 if unavailable)

### Intelligence
- **TEMPR** (§3.8) — 5-strategy retrieval with Reciprocal Rank Fusion (k=60):
  1. Semantic (vector cosine)
  2. BM25 (fulltext)
  3. Graph (entity neighborhood)
  4. Temporal (time-weighted)
  5. Recency (freshness boost)

- **Consolidator** (§3.5) — Observation synthesis with evidence tracking:
  - Groups observations by entity+predicate keys
  - Detects contradictions (SUPPORTS/CONTRADICTS/SUPERSEDES edges)
  - Redis queue for async processing

- **Mental Models** (§3.6-3.7) — Persona archetypes with curated dispositions:
  - Skepticism, literalism, empathy modes
  - CURATED_FOR edges linking models to entities
  - LLM-assisted drafting (GLM-5.2)

### Infrastructure
- **Circuit Breaker** (§8.1) — CLOSED → OPEN → HALF_OPEN state machine
  - 5 failure threshold, 60s cooldown
  - Protects all MCP + API calls
  - Manual reset via `POST /circuit-breaker/reset`

- **Observability** (§8) — HealthMonitor, AlertManager, MetricsCollector
  - Structured logging
  - Health checks: Neo4j, Redis, queue depth, ingestion, contradictions

- **Eval Harness** (§13.2) — 30-query test suite
  - Metrics: Recall@5, Recall@10, MRR
  - Baseline: Recall@5=0.43, MRR=0.43 (at 3% data migration)

## API Reference

### Public Endpoints (no auth)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web UI |
| GET | `/health` | Neo4j + Redis + queue status |
| GET | `/metrics` | Detailed metrics + health checks |
| GET | `/circuit-breaker` | Circuit breaker state |
| POST | `/circuit-breaker/reset` | Reset circuit breaker |

### Protected Endpoints (Bearer token)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/search?query=...&limit=10` | BM25 fulltext search |
| GET | `/entities/{id}` | Entity with facts (subject + object) |
| GET | `/history/{id}?mode=knowledge&at_time=...` | Temporal history |
| POST | `/store` | Queue content for ingestion |
| POST | `/tempr` | TEMPR multi-strategy search |
| GET | `/mental-models?query=...` | Search mental models |
| POST | `/consolidate` | Run observation consolidation |
| GET | `/eval` | Run eval harness |

### MCP Tools (10)
1. `ferrite_search` — Search facts by keywords
2. `ferrite_query` — Natural language → Cypher
3. `ferrite_entity_facts` — Get facts for an entity
4. `ferrite_multi_hop` — Multi-hop graph traversal
5. `ferrite_inject` — Auto-inject context (TEMPR + score floor)
6. `ferrite_stats` — Graph statistics
7. `ferrite_ingest` — Ingest content
8. `ferrite_tempr_search` — TEMPR 5-strategy retrieval
9. `ferrite_mental_model` — Mental model search
10. `ferrite_consolidate` — Run consolidation

## Configuration

Environment variables (all optional, defaults shown):

```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=ferrite123
REDIS_URL=redis://localhost:6379
FERRITE_API_KEY=          # Set to enforce bearer auth
NAMESPACE_DEFAULT=shared
WRITE_RATE_LIMIT=100      # Per minute
READ_RATE_LIMIT=200       # Per minute
```

## Docker

```bash
# Build and start all services
docker compose up -d

# View logs
docker logs ferrite-api -f

# Rebuild after code changes
docker compose build ferrite-api && docker compose up -d ferrite-api

# Health check
curl http://localhost:8001/health
```

## Testing

```bash
# Unit tests (174)
uv run pytest

# E2E tests (38) — requires running Docker stack
uv run python scripts/e2e_test.py

# Eval harness
curl http://localhost:8001/eval

# Lint
uv run ruff check src/ tests/ scripts/
```

## CI Pipeline

GitHub Actions (`.github/workflows/ci.yml`):
1. **lint** — ruff check
2. **test** — 174 unit tests (Neo4j + Redis service containers)
3. **e2e** — 38 E2E tests against live API
4. **eval** — Recall@5 ≥ 0.30 regression gate

## Backup & Recovery

```bash
# Backup Neo4j
scripts/backup.sh

# Restore
scripts/restore.sh /path/to/backup.dump

# Health check
scripts/health_check.sh
```

## Project Structure

```
ferrite/
├── src/ferrite/
│   ├── api.py              # FastAPI REST + Web UI
│   ├── mcp_server.py       # MCP server (10 tools)
│   ├── ingestion.py        # Pipeline + LLM extraction
│   ├── query.py            # Auto-inject v2 (TEMPR + budget)
│   ├── tempr.py            # 5-strategy RRF retrieval
│   ├── consolidator.py     # Observation synthesis
│   ├── mental_models.py    # Persona archetypes
│   ├── circuit_breaker.py  # State machine
│   ├── embeddings.py       # Ollama 768d + VectorStore
│   ├── temporal.py         # as_of_knowledge/world
│   ├── schema.py           # Neo4j constraints + indexes
│   ├── metrics.py          # MetricsCollector
│   ├── observability.py    # HealthMonitor + AlertManager
│   ├── eval.py             # Recall@K + MRR harness
│   ├── config.py           # Settings (pydantic-settings)
│   ├── models.py           # Pydantic models
│   └── static/             # Web UI
├── tests/                  # 174 unit tests
├── scripts/               # E2E, backup, migration
├── eval/queries.yaml       # Eval test dataset
├── docker-compose.yml      # Neo4j + Redis + API
├── Dockerfile              # Python 3.12-slim + uv
└── .github/workflows/ci.yml
```

## License

MIT
