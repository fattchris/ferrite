#!/usr/bin/env bash
# Ferrite log tailing and search utility.
#
# Usage:
#   ./scripts/logs.sh              # tail ferrite-api logs (live)
#   ./scripts/logs.sh -f           # same, explicit follow
#   ./scripts/logs.sh --tail 100   # last 100 lines
#   ./scripts/logs.sh --search "ERROR"   # grep for errors
#   ./scripts/logs.sh --search "episode" --tail 500
#   ./scripts/logs.sh neo4j        # tail neo4j logs instead
#   ./scripts/logs.sh redis        # tail redis logs
#   ./scripts/logs.sh all          # tail all containers
#   ./scripts/logs.sh --help       # show this help
#
# JSON logs are structured: {"timestamp":"...","level":"INFO","module":"...","message":"..."}
# Pretty-print with: ./scripts/logs.sh | jq .
set -euo pipefail

CONTAINER="ferrite-api"
FOLLOW=""
TAIL=""
SEARCH=""

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
    exit 0
}

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--follow) FOLLOW="--follow"; shift ;;
        --tail) TAIL="--tail ${2:?--tail requires a number}"; shift 2 ;;
        --tail=*) TAIL="--tail ${1#*=}"; shift ;;
        --search) SEARCH="$2"; shift 2 ;;
        --search=*) SEARCH="${1#*=}"; shift ;;
        --help|-h) usage ;;
        neo4j|redis|ferrite-api|all) CONTAINER="$1"; shift ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

# If "all", tail all containers
if [[ "$CONTAINER" == "all" ]]; then
    echo "Tailing all Ferrite containers (Ctrl+C to stop)..."
    docker compose -f docker-compose.prod.yml logs ${FOLLOW} ${TAIL} 2>/dev/null || \
        docker logs ${FOLLOW} ${TAIL} ferrite-api ferrite-neo4j ferrite-redis 2>&1
    exit 0
fi

# Build docker logs command
CMD="docker logs ${FOLLOW} ${TAIL} ${CONTAINER}"

# Apply search filter if requested
if [[ -n "$SEARCH" ]]; then
    echo "Searching ${CONTAINER} logs for: ${SEARCH}"
    if [[ -n "$TAIL" ]]; then
        eval "$CMD 2>&1 | grep -i '${SEARCH}'" | head -200
    else
        eval "$CMD 2>&1 | grep -i '${SEARCH}'"
    fi
else
    eval "$CMD 2>&1"
fi
