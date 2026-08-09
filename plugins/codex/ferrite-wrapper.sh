#!/usr/bin/env bash
# Codex CLI wrapper for Ferrite auto-ingestion
# ============================================
# Codex CLI has no session hooks (feature request #13056).
# This wrapper captures session output and POSTs to Ferrite on exit.
#
# USAGE:
#   alias codex='~/.codex/ferrite-wrapper.sh'
#
# INSTALL:
#   chmod +x ~/.codex/ferrite-wrapper.sh
#   Add alias to ~/.bashrc or ~/.zshrc:
#     alias codex='~/.codex/ferrite-wrapper.sh'

set -euo pipefail

FERRITE_ENDPOINT="${FERRITE_ENDPOINT:-http://localhost:8001}"
FERRITE_API_KEY="${FERRITE_API_KEY:-}"
FERRITE_NAMESPACE="${FERRITE_NAMESPACE:-default}"
SESSION_ID="codex-$(date +%Y%m%d_%H%M%S)"
CAPTURE_FILE="/tmp/codex-session-${SESSION_ID}.log"

# Run codex, capture all output
echo "[ferrite] Session ${SESSION_ID} starting..." >&2
codex "$@" 2>&1 | tee "$CAPTURE_FILE"
EXIT_CODE=$?

# Post session to Ferrite on exit
if [ -n "$FERRITE_API_KEY" ] && [ -s "$CAPTURE_FILE" ]; then
    echo "[ferrite] Ingesting session ${SESSION_ID}..." >&2
    curl -s -X POST \
        "${FERRITE_ENDPOINT}/ingest/session" \
        -H "Authorization: Bearer ${FERRITE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "{
            \"session_id\": \"${SESSION_ID}\",
            \"agent\": \"codex\",
            \"namespace\": \"${FERRITE_NAMESPACE}\",
            \"transcript_path\": \"${CAPTURE_FILE}\"
        }" || echo "[ferrite] Ingestion failed (server not running?)" >&2
fi

# Cleanup
rm -f "$CAPTURE_FILE" 2>/dev/null || true
exit $EXIT_CODE
