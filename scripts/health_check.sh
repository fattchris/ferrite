#!/usr/bin/env bash
set -uo pipefail

# Ferrite Health Check Script
# Checks Neo4j, Redis, queue depth, and ferrite-api.
# Exit 0 if all healthy, 1 if any down.

# Resolve project directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

HEALTHY=0

# 1. Neo4j — cypher RETURN 1
echo -n "[Neo4j]  Checking... "
NEO4J_RESULT=$(docker compose exec -T neo4j cypher -a bolt://localhost:7687 -u neo4j -p ferrite123 "RETURN 1" 2>&1)
if echo "$NEO4J_RESULT" | grep -q "1"; then
  echo "OK"
else
  echo "FAIL"
  echo "  $NEO4J_RESULT"
  HEALTHY=1
fi

# 2. Redis — PING
echo -n "[Redis]  Checking... "
REDIS_RESULT=$(docker compose exec -T redis redis-cli PING 2>&1)
if echo "$REDIS_RESULT" | grep -q "PONG"; then
  echo "OK"
else
  echo "FAIL"
  echo "  $REDIS_RESULT"
  HEALTHY=1
fi

# 3. Queue depth — LLEN ferrite:queue
echo -n "[Queue]  Checking depth... "
QUEUE_DEPTH=$(docker compose exec -T redis redis-cli LLEN ferrite:queue 2>&1 | tr -d '[:space:]')
if [[ "$QUEUE_DEPTH" =~ ^[0-9]+$ ]]; then
  if [ "$QUEUE_DEPTH" -lt 1000 ]; then
    echo "OK (depth: $QUEUE_DEPTH)"
  else
    echo "WARN (depth: $QUEUE_DEPTH — exceeds threshold 1000)"
    HEALTHY=1
  fi
else
  echo "FAIL"
  echo "  $QUEUE_DEPTH"
  HEALTHY=1
fi

# 4. ferrite-api — curl /health
echo -n "[API]    Checking... "
API_RESULT=$(curl -sf -m 5 http://localhost:8001/health 2>&1)
if [ $? -eq 0 ]; then
  echo "OK"
  echo "  $API_RESULT"
else
  echo "FAIL"
  HEALTHY=1
fi

echo ""
if [ $HEALTHY -eq 0 ]; then
  echo "All services healthy."
  exit 0
else
  echo "One or more services unhealthy."
  exit 1
fi
