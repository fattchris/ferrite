#!/usr/bin/env bash
set -euo pipefail

# Ferrite Backup Script (A12)
# Nightly: stop writers, BGSAVE redis, stop neo4j, dump DB, copy volumes,
# combine into dated tar.gz, 30-day retention, restart services.

BACKUP_DIR="/backups"
DATE=$(date +%Y%m%d)
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

# Resolve the project directory (where this script lives is scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== Ferrite Backup — $DATE ==="

mkdir -p "$BACKUP_DIR"

# 1. Stop ferrite-api (drains writers; in-proc consumer stops)
echo "[1/7] Stopping ferrite-api..."
docker compose -f "$COMPOSE_FILE" stop ferrite-api

# 2. BGSAVE Redis (non-blocking save, then wait)
echo "[2/7] BGSAVE Redis..."
docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli BGSAVE
sleep 5

# 3. Stop Neo4j (dump must be of a stopped DB)
echo "[3/7] Stopping Neo4j..."
docker compose -f "$COMPOSE_FILE" stop neo4j

# 4. Neo4j admin database dump via --entrypoint
#    --entrypoint bypasses the image's server-start default
echo "[4/7] Dumping Neo4j database..."
docker compose -f "$COMPOSE_FILE" run --rm --entrypoint neo4j-admin \
  -v "${BACKUP_DIR}:/backups" \
  neo4j database dump neo4j \
  --to-path="/backups/dump-${DATE}"

# 5. Copy Docker volumes from inside a container
#    macOS can't reach named volumes from the host filesystem (F-7)
echo "[5/7] Copying Docker volumes..."
docker run --rm \
  -v ferrite_redis_data:/src/redis:ro \
  -v ferrite_api_data:/src/api:ro \
  -v "${BACKUP_DIR}:/backups" \
  alpine tar -czf "/backups/ferrite-${DATE}-volumes.tar.gz" /src

# 6. Combine volumes tar + neo4j dump into final backup
echo "[6/7] Combining into final backup tarball..."
tar -czf "${BACKUP_DIR}/ferrite-${DATE}.tar.gz" \
  -C "$BACKUP_DIR" "dump-${DATE}" \
  -C "$BACKUP_DIR" "ferrite-${DATE}-volumes.tar.gz"

# Clean up intermediate files
rm -f "${BACKUP_DIR}/ferrite-${DATE}-volumes.tar.gz"
rm -rf "${BACKUP_DIR}/dump-${DATE}"

# 7. Retention: delete backups older than 30 days
echo "[7/7] Pruning backups older than 30 days..."
find "$BACKUP_DIR" -name "ferrite-*.tar.gz" -mtime +30 -delete

# Restart services
echo "Restarting Neo4j and ferrite-api..."
docker compose -f "$COMPOSE_FILE" start neo4j ferrite-api

echo "=== Backup complete: ${BACKUP_DIR}/ferrite-${DATE}.tar.gz ==="
