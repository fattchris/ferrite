#!/usr/bin/env bash
set -euo pipefail

# Ferrite Restore Script (A12)
# Usage: ./scripts/restore.sh YYYYMMDD
# Stops containers, loads Neo4j dump, restores volumes, restarts.

BACKUP_DIR="/backups"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <YYYYMMDD>"
  echo "Example: $0 20260801"
  exit 1
fi

DATE="$1"
BACKUP_FILE="${BACKUP_DIR}/ferrite-${DATE}.tar.gz"

# Resolve project directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "ERROR: Backup file not found: $BACKUP_FILE"
  exit 1
fi

echo "=== Ferrite Restore — $DATE ==="

# 1. Stop all containers
echo "[1/5] Stopping all containers..."
docker compose -f "$COMPOSE_FILE" down

# 2. Extract backup tarball
echo "[2/5] Extracting backup..."
mkdir -p "${BACKUP_DIR}/restore-${DATE}"
tar -xzf "$BACKUP_FILE" -C "${BACKUP_DIR}/restore-${DATE}"

# 3. Load Neo4j database dump
echo "[3/5] Loading Neo4j database..."
docker compose -f "$COMPOSE_FILE" run --rm --entrypoint neo4j-admin \
  -v "${BACKUP_DIR}:/backups" \
  neo4j database load neo4j \
  --from-path="/backups/restore-${DATE}/dump-${DATE}"

# 4. Restore volumes from tar (overwrite existing volume contents)
echo "[4/5] Restoring Docker volumes..."
docker run --rm \
  -v ferrite_redis_data:/dst/redis \
  -v ferrite_api_data:/dst/api \
  -v "${BACKUP_DIR}:/backups" \
  alpine sh -c "tar -xzf /backups/restore-${DATE}/ferrite-${DATE}-volumes.tar.gz -C /tmp && cp -a /tmp/src/redis/. /dst/redis/ && cp -a /tmp/src/api/. /dst/api/"

# Clean up extracted files
rm -rf "${BACKUP_DIR}/restore-${DATE}"

# 5. Start containers
echo "[5/5] Starting containers..."
docker compose -f "$COMPOSE_FILE" up -d

echo "=== Restore complete from $DATE ==="
echo "Verify with: ./scripts/health_check.sh"
