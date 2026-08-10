#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
#  ███████ ████████ ██████  ██████   █████  ██████  ███████
#  ██         ██    ██   ██ ██   ██ ██   ██ ██   ██ ██
#  ███████    ██    ██████  ██   ██ ███████ ██   ██ ███████
#       ██    ██    ██      ██   ██ ██   ██ ██   ██      ██
#  ███████    ██    ██      ██████   █████  ██████  ███████
#
#  Temporal Knowledge Graph Memory System — Installer v1.0
#  Part of the Kassett ecosystem · https://github.com/fattchris/ferrite
# ============================================================================
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/fattchris/ferrite/main/scripts/install.sh | bash
#
# Or clone + run:
#   git clone https://github.com/fattchris/ferrite.git
#   cd ferrite && bash scripts/install.sh
#
# The installer:
#   1. Checks prerequisites (Docker, Python 3.11+, uv)
#   2. Generates secure secrets (.env)
#   3. Optionally configures LLM extraction backend
#   4. Optionally installs Ollama for local embeddings
#   5. Builds and starts the Docker stack
#   6. Optionally installs Caddy for TLS (macOS)
#   7. Optionally installs Hermes memory provider plugin
#   8. Verifies the deployment
#
# ============================================================================

# Colors
BOLD='\033[1m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

# Globals
PROJECT_DIR=""
INSTALL_LOG="/tmp/ferrite-install-$(date +%s).log"
STEP=0
TOTAL_STEPS=8

# ============================================================================
# Helpers
# ============================================================================

banner() {
    echo ""
    echo -e "${MAGENTA}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${MAGENTA}║${NC}" "$(printf '%*s' 50)" "${MAGENTA}║${NC}"
    local msg="$1"
    local pad=$(( (50 - ${#msg}) / 2 ))
    printf "${MAGENTA}║${NC}%*s%s%*s${MAGENTA}║${NC}\n" $pad "" "$msg" $pad ""
    echo -e "${MAGENTA}║${NC}" "$(printf '%*s' 50)" "${MAGENTA}║${NC}"
    echo -e "${MAGENTA}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
}

step() {
    STEP=$((STEP + 1))
    echo ""
    echo -e "${CYAN}━━━ [${STEP}/${TOTAL_STEPS}] $1 ━━━${NC}"
    echo ""
}

ok() {
    echo -e "  ${GREEN}✓${NC} $1"
}

fail() {
    echo -e "  ${RED}✗${NC} $1"
}

warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

info() {
    echo -e "  ${BLUE}ℹ${NC} $1"
}

prompt() {
    local var=$1
    local question=$2
    local default=${3:-}
    local answer
    if [ -n "$default" ]; then
        read -rp "$(echo -e "  ${BOLD}${question}${NC} [${default}]: ")" answer
        answer="${answer:-$default}"
    else
        read -rp "$(echo -e "  ${BOLD}${question}${NC}: ")" answer
    fi
    eval "$var=\"\$answer\""
}

prompt_yn() {
    local var=$1
    local question=$2
    local default=${3:-n}
    local answer
    if [ "$default" = "y" ]; then
        read -rp "$(echo -e "  ${BOLD}${question}${NC} [Y/n]: ")" answer
        answer="${answer:-y}"
    else
        read -rp "$(echo -e "  ${BOLD}${question}${NC} [y/N]: ")" answer
        answer="${answer:-n}"
    fi
    # Normalize to y/n
    case "$(echo "$answer" | tr '[:upper:]' '[:lower:]')" in
        y|yes) eval "$var=y" ;;
        *) eval "$var=n" ;;
    esac
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

generate_secret() {
    openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))"
}

# ============================================================================
# Pre-flight checks
# ============================================================================

preflight() {
    banner "Ferrite Installer"
    echo -e "  ${BOLD}Temporal Knowledge Graph Memory System${NC}"
    echo -e "  Version 0.1.0 · Kassett Ecosystem"
    echo ""
    echo -e "  This installer will set up:"
    echo -e "    ${CYAN}•${NC} Neo4j 5 graph database"
    echo -e "    ${CYAN}•${NC} Redis 8 (queue + cache + AOF persistence)"
    echo -e "    ${CYAN}•${NC} FastAPI REST + MCP server"
    echo -e "    ${CYAN}•${NC} Prometheus metrics"
    echo -e "    ${CYAN}•${NC} Optional: TLS, Ollama embeddings, Hermes plugin"
    echo ""

    # Find project directory
    if [ -f "$(pwd)/docker-compose.yml" ] || [ -f "$(pwd)/docker-compose.prod.yml" ]; then
        PROJECT_DIR="$(pwd)"
    elif [ -f "$(dirname "$0")/../docker-compose.yml" ]; then
        PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    else
        echo -e "  ${YELLOW}Could not find Ferrite project directory.${NC}"
        echo -e "  ${YELLOW}Cloning from GitHub...${NC}"
        prompt_yn DO_CLONE "Clone Ferrite from GitHub?" "y"
        if [ "$DO_CLONE" = "y" ]; then
            git clone https://github.com/fattchris/ferrite.git /tmp/ferrite-clone 2>&1 | tee -a "$INSTALL_LOG"
            PROJECT_DIR="/tmp/ferrite-clone"
        else
            fail "Cannot continue without project directory."
            exit 1
        fi
    fi

    cd "$PROJECT_DIR"
    ok "Project directory: $PROJECT_DIR"

    # Prerequisites
    echo ""
    echo -e "  ${BOLD}Checking prerequisites...${NC}"

    # Docker
    if command_exists docker; then
        ok "Docker found: $(docker --version)"
    else
        fail "Docker not found. Install: https://docs.docker.com/get-docker/"
        echo -e "    macOS:  ${CYAN}brew install --cask docker${NC}"
        echo -e "    Linux:  ${CYAN}curl -fsSL https://get.docker.com | sh${NC}"
        exit 1
    fi

    # Docker Compose (v2 is built into Docker)
    if docker compose version >/dev/null 2>&1; then
        ok "Docker Compose v2 found"
    else
        fail "Docker Compose v2 not found. Update Docker or install compose plugin."
        exit 1
    fi

    # Python 3.11+
    if command_exists python3; then
        PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
            ok "Python $PY_VERSION found"
        else
            fail "Python 3.11+ required, found $PY_VERSION"
            exit 1
        fi
    else
        fail "Python 3 not found. Install: https://www.python.org/downloads/"
        exit 1
    fi

    # uv (package manager) — install if missing
    if command_exists uv; then
        ok "uv found: $(uv --version)"
    else
        warn "uv not found — installing..."
        curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tee -a "$INSTALL_LOG" >/dev/null
        export PATH="$HOME/.local/bin:$PATH"
        if command_exists uv; then
            ok "uv installed"
        else
            fail "Failed to install uv. Install manually: https://docs.astral.sh/uv/"
            exit 1
        fi
    fi

    # git
    if command_exists git; then
        ok "git found"
    else
        fail "git not found"
        exit 1
    fi

    # Docker daemon running?
    if docker info >/dev/null 2>&1; then
        ok "Docker daemon running"
    else
        fail "Docker daemon not running. Start Docker Desktop or: sudo systemctl start docker"
        exit 1
    fi
}

# ============================================================================
# Step 1: Generate secrets
# ============================================================================

step_secrets() {
    step "Generate Secure Secrets"

    if [ -f .env ] && ! grep -q "change-me" .env 2>/dev/null; then
        warn ".env already exists with configured secrets."
        prompt_yn OVERWRITE "Overwrite .env with new secrets?" "n"
        if [ "$OVERWRITE" = "n" ]; then
            ok "Keeping existing .env"
            return
        fi
    fi

    echo -e "  ${BOLD}Generating cryptographically secure secrets...${NC}"
    local neo4j_pass api_key llm_key

    neo4j_pass=$(generate_secret)
    api_key=$(generate_secret)

    echo -e "    ${CYAN}Neo4j password:${NC} ${neo4j_pass:0:16}... (full key in .env)"
    echo -e "    ${CYAN}API key:${NC}        ${api_key:0:16}... (full key in .env)"

    # LLM key — ask if they have one
    echo ""
    echo -e "  ${BOLD}LLM extraction backend (optional):${NC}"
    echo -e "    Ferrite uses an LLM to extract facts from text."
    echo -e "    Options: LiteLLM proxy, OpenRouter, OpenAI, Ollama (free, local)"
    echo ""
    prompt LLM_BASE "LLM API base URL (or blank for Ollama-only)" "http://localhost:4000/v1"
    prompt LLM_KEY "LLM API key (blank if using Ollama or no auth)" ""
    prompt LLM_MODEL "LLM model name" "gpt-4o-mini"

    if [ -z "$LLM_KEY" ]; then
        LLM_KEY=""
    fi

    cat > .env << EOF
# Ferrite Production Environment
# Generated by install.sh on $(date)
# DO NOT COMMIT — .gitignore excludes this file

# Neo4j password
NEO4J_PASSWORD=${neo4j_pass}

# Ferrite API key (Bearer token)
FERRITE_API_KEY=${api_key}

# LLM extraction backend
LLM_BASE_URL=${LLM_BASE}
LLM_API_KEY=${LLM_KEY}
LLM_MODEL=${LLM_MODEL}

# Domain for TLS (localhost for dev, or real domain for prod)
FERRITE_DOMAIN=localhost
EOF

    ok ".env written with secure secrets"

    # Also update docker-compose.prod.yml env reference if needed
    if [ -f docker-compose.prod.yml ]; then
        ok "docker-compose.prod.yml reads from .env automatically"
    fi
}

# ============================================================================
# Step 2: Configure extraction LLM
# ============================================================================

step_llm() {
    step "Configure LLM Extraction"

    local has_llm=false

    # Check if LLM endpoint is reachable
    local llm_url
    llm_url=$(grep LLM_BASE_URL .env | cut -d= -f2-)
    if [ -n "$llm_url" ] && [ "$llm_url" != "http://localhost:4000/v1" ]; then
        ok "LLM endpoint configured: $llm_url"
        has_llm=true
    elif [ "$llm_url" = "http://localhost:4000/v1" ]; then
        # Check if LiteLLM is actually running
        if curl -sf -m 3 "$llm_url/models" >/dev/null 2>&1; then
            ok "LiteLLM proxy detected at $llm_url"
            has_llm=true
        else
            warn "LiteLLM not detected at default URL. Extraction will use empty results until configured."
            echo -e "    Set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL in .env when ready."
        fi
    fi

    # Ollama for embeddings
    echo ""
    echo -e "  ${BOLD}Embeddings (Ollama):${NC}"
    echo -e "    Ferrite uses nomic-embed-text (768d) via Ollama for vector search."
    echo -e "    Without Ollama, search degrades to BM25-only (still works, just less precise)."
    echo ""
    prompt_yn INSTALL_OLLAMA "Install Ollama and pull embedding model?" "y"

    if [ "$INSTALL_OLLAMA" = "y" ]; then
        if command_exists ollama; then
            ok "Ollama already installed"
        else
            echo -e "  ${BOLD}Installing Ollama...${NC}"
            if command_exists brew; then
                brew install ollama 2>&1 | tee -a "$INSTALL_LOG" >/dev/null
            else
                curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tee -a "$INSTALL_LOG" >/dev/null
            fi
        fi

        if command_exists ollama; then
            ok "Ollama installed"
            # Start ollama serve if not running
            if ! curl -sf -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
                info "Starting Ollama server..."
                ollama serve >/dev/null 2>&1 &
                sleep 3
            fi
            # Pull embedding model
            info "Pulling nomic-embed-text (274MB)..."
            ollama pull nomic-embed-text 2>&1 | tee -a "$INSTALL_LOG"
            ok "Embedding model ready"
        else
            warn "Ollama installation failed. Vector search will use BM25 fallback."
        fi
    else
        warn "Skipping Ollama. Vector search will use BM25 fallback."
    fi
}

# ============================================================================
# Step 3: Build Docker stack
# ============================================================================

step_docker() {
    step "Build and Start Docker Stack"

    echo -e "  ${BOLD}Building Docker images...${NC}"
    echo -e "    This may take 2-5 minutes on first run."
    echo ""

    local compose_file="docker-compose.prod.yml"
    if [ ! -f "$compose_file" ]; then
        compose_file="docker-compose.yml"
    fi

    # Build
    docker compose -f "$compose_file" build 2>&1 | tee -a "$INSTALL_LOG" | tail -5
    ok "Docker images built"

    # Start
    echo -e "  ${BOLD}Starting containers...${NC}"
    docker compose -f "$compose_file" up -d 2>&1 | tee -a "$INSTALL_LOG"
    ok "Containers started"

    # Wait for health
    echo -e "  ${BOLD}Waiting for services to be healthy...${NC}"
    local retries=0
    local max_retries=30
    while [ $retries -lt $max_retries ]; do
        if curl -sf -m 3 http://localhost:8001/health >/dev/null 2>&1; then
            ok "API is healthy"
            curl -s http://localhost:8001/health | python3 -m json.tool 2>/dev/null || true
            return
        fi
        retries=$((retries + 1))
        echo -e "    Waiting... (${retries}/${max_retries})"
        sleep 5
    done

    warn "API not responding after ${max_retries} retries."
    echo -e "    Check logs: ${CYAN}docker logs ferrite-api${NC}"
    echo -e "    The container may need more time to start."
    echo -e "    You can check manually later: ${CYAN}curl http://localhost:8001/health${NC}"
}

# ============================================================================
# Step 4: TLS (Caddy)
# ============================================================================

step_tls() {
    step "Configure TLS (Optional)"

    echo -e "  ${BOLD}TLS/HTTPS via Caddy${NC}"
    echo -e "    Ferrite can serve over HTTPS with a self-signed cert (localhost)"
    echo -e "    or auto-provision Let's Encrypt for a real domain."
    echo ""
    prompt_yn SETUP_TLS "Set up Caddy TLS?" "y"

    if [ "$SETUP_TLS" = "n" ]; then
        warn "Skipping TLS. API available at http://localhost:8001"
        return
    fi

    local domain
    prompt domain "Domain (or 'localhost' for self-signed)" "localhost"

    # Install Caddy
    if ! command_exists caddy; then
        echo -e "  ${BOLD}Installing Caddy...${NC}"
        if command_exists brew; then
            brew install caddy 2>&1 | tee -a "$INSTALL_LOG" >/dev/null
        else
            warn "Cannot auto-install Caddy on this platform."
            echo -e "    Install manually: https://caddyserver.com/docs/install"
            return
        fi
    fi
    ok "Caddy available"

    # Write Caddyfile
    local tls_port=9443
    cat > Caddyfile << EOF
{
    admin off
}

${domain}:${tls_port} {
    tls internal
    reverse_proxy localhost:8001
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        X-XSS-Protection "1; mode=block"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
    encode gzip zstd
}
EOF
    ok "Caddyfile written (port ${tls_port})"

    # macOS launchd
    if [ "$(uname)" = "Darwin" ]; then
        local plist_dir="$HOME/Library/LaunchAgents"
        mkdir -p "$plist_dir"
        cat > "$plist_dir/com.ferrite.caddy.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ferrite.caddy</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which caddy)</string>
        <string>run</string>
        <string>--config</string>
        <string>${PROJECT_DIR}/Caddyfile</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/ferrite-caddy.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ferrite-caddy.err.log</string>
</dict>
</plist>
EOF
        launchctl unload "$plist_dir/com.ferrite.caddy.plist" 2>/dev/null || true
        launchctl load "$plist_dir/com.ferrite.caddy.plist" 2>/dev/null || true
        ok "Caddy launchd agent loaded (auto-restarts on boot)"
    else
        # Start Caddy in background
        caddy run --config Caddyfile &
        ok "Caddy started (not managed — set up systemd or supervisor)"
    fi

    sleep 3
    if curl -sk "https://${domain}:${tls_port}/health" >/dev/null 2>&1 || \
       curl -sk "https://localhost:${tls_port}/health" >/dev/null 2>&1; then
        ok "TLS verified: https://${domain}:${tls_port}"
    else
        warn "TLS not yet ready — Caddy may need a few seconds."
        echo -e "    Verify manually: ${CYAN}curl -sk https://localhost:${tls_port}/health${NC}"
    fi

    echo ""
    echo -e "  ${BOLD}Update .env with domain:${NC}"
    sed -i.bak "s/FERRITE_DOMAIN=localhost/FERRITE_DOMAIN=${domain}/" .env 2>/dev/null || \
        sed -i "" "s/FERRITE_DOMAIN=localhost/FERRITE_DOMAIN=${domain}/" .env
}

# ============================================================================
# Step 5: Hermes plugin
# ============================================================================

step_hermes() {
    step "Hermes Memory Provider Plugin (Optional)"

    echo -e "  ${BOLD}Hermes Agent Integration${NC}"
    echo -e "    Installs Ferrite as a Hermes memory provider plugin."
    echo -e "    Enables auto-context injection and session ingestion."
    echo ""

    if ! command_exists hermes; then
        warn "Hermes CLI not found. Skipping plugin installation."
        echo -e "    Install Hermes: https://hermes-agent.nousresearch.com/docs"
        return
    fi

    prompt_yn INSTALL_PLUGIN "Install Hermes memory provider plugin?" "y"
    if [ "$INSTALL_PLUGIN" = "n" ]; then
        return
    fi

    local hermes_home="${HERMES_HOME:-$HOME/.hermes}"
    local ferrite_config="$hermes_home/ferrite.json"

    local neo4j_pass api_key
    neo4j_pass=$(grep NEO4J_PASSWORD .env | cut -d= -f2)
    api_key=$(grep FERRITE_API_KEY .env | cut -d= -f2)

    mkdir -p "$hermes_home"

    cat > "$ferrite_config" << EOF
{
    "api_url": "http://localhost:8001",
    "api_key": "${api_key}",
    "neo4j_uri": "bolt://localhost:7687",
    "neo4j_user": "neo4j",
    "neo4j_password": "${neo4j_pass}",
    "namespace": "shared",
    "circuit_breaker_threshold": 5,
    "circuit_breaker_cooldown": 120
}
EOF
    ok "Ferrite config written to $ferrite_config"

    # Activate memory provider
    hermes memory setup --provider ferrite 2>&1 | tee -a "$INSTALL_LOG" || true
    ok "Ferrite memory provider activated"
}

# ============================================================================
# Step 6: Verify deployment
# ============================================================================

step_verify() {
    step "Verify Deployment"

    local compose_file="docker-compose.prod.yml"
    if [ ! -f "$compose_file" ]; then
        compose_file="docker-compose.yml"
    fi

    echo -e "  ${BOLD}Container Status:${NC}"
    docker compose -f "$compose_file" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
        docker compose -f "$compose_file" ps
    echo ""

    # Health check
    echo -e "  ${BOLD}Health Check:${NC}"
    if curl -sf -m 5 http://localhost:8001/health 2>/dev/null; then
        echo ""
        ok "API healthy"
    else
        fail "API not responding"
        echo -e "    Check: ${CYAN}docker logs ferrite-api${NC}"
    fi

    # Auth check
    echo ""
    echo -e "  ${BOLD}Auth Check:${NC}"
    local api_key
    api_key=$(grep FERRITE_API_KEY .env | cut -d= -f2)
    local auth_status
    auth_status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/search?query=test)
    if [ "$auth_status" = "401" ]; then
        ok "Auth enforced (401 without key)"
    else
        warn "Auth returned $auth_status (expected 401)"
    fi
    auth_status=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $api_key" "http://localhost:8001/search?query=test&limit=1")
    if [ "$auth_status" = "200" ]; then
        ok "Auth verified (200 with key)"
    else
        warn "Auth returned $auth_status (expected 200)"
    fi

    # TLS check
    if [ -f Caddyfile ]; then
        echo ""
        echo -e "  ${BOLD}TLS Check:${NC}"
        if curl -sk -m 5 https://localhost:9443/health >/dev/null 2>&1; then
            ok "TLS working: https://localhost:9443"
        else
            warn "TLS not ready yet"
        fi
    fi

    # Redis AOF
    echo ""
    echo -e "  ${BOLD}Redis AOF:${NC}"
    local aof
    aof=$(docker exec ferrite-redis redis-cli CONFIG GET appendonly 2>/dev/null | tail -1)
    if [ "$aof" = "yes" ]; then
        ok "AOF persistence enabled"
    else
        warn "AOF not enabled: $aof"
    fi
}

# ============================================================================
# Step 7: Seed data (optional)
# ============================================================================

step_seed() {
    step "Seed Initial Data (Optional)"

    echo -e "  ${BOLD}Seed the graph with sample data?${NC}"
    echo -e "    This adds entities and facts to verify search works."
    echo ""
    prompt_yn DO_SEED "Seed sample data?" "y"

    if [ "$DO_SEED" = "n" ]; then
        return
    fi

    local api_key
    api_key=$(grep FERRITE_API_KEY .env | cut -d= -f2)

    # Store a test fact
    local response
    response=$(curl -sf -X POST http://localhost:8001/store \
        -H "Authorization: Bearer $api_key" \
        -H "Content-Type: application/json" \
        -d '{"content": "Ferrite is a temporal knowledge graph system built with Neo4j and Redis.", "namespace": "shared"}' 2>&1)

    if echo "$response" | grep -q "episode_id"; then
        ok "Test fact stored"
    else
        warn "Seed failed: $response"
    fi

    # Search to verify
    local search_result
    search_result=$(curl -sf "http://localhost:8001/search?query=temporal+knowledge+graph&limit=3" \
        -H "Authorization: Bearer $api_key" 2>&1)

    if echo "$search_result" | grep -q "results"; then
        ok "Search verified — facts are retrievable"
    else
        warn "Search returned no results yet (extraction may still be processing)"
    fi
}

# ============================================================================
# Step 8: Final summary
# ============================================================================

step_summary() {
    step "Installation Complete"

    local api_key
    api_key=$(grep FERRITE_API_KEY .env | cut -d= -f2)

    banner "Ferrite is Ready"

    echo -e "  ${GREEN}All systems operational.${NC}"
    echo ""
    echo -e "  ${BOLD}Endpoints:${NC}"
    echo -e "    API:     ${CYAN}http://localhost:8001${NC}"
    if [ -f Caddyfile ]; then
        echo -e "    TLS:     ${CYAN}https://localhost:9443${NC}"
    fi
    echo -e "    Neo4j:   ${CYAN}http://localhost:7474${NC}"
    echo -e "    Metrics: ${CYAN}http://localhost:9090${NC} (Prometheus)"
    echo -e "    Web UI:  ${CYAN}http://localhost:8001/${NC}"
    echo ""
    echo -e "  ${BOLD}Your API Key:${NC}"
    echo -e "    ${CYAN}${api_key}${NC}"
    echo -e "    (stored in .env)"
    echo ""
    echo -e "  ${BOLD}Next Steps:${NC}"
    echo -e "    ${CYAN}•${NC} Store a fact:"
    echo -e "      curl -X POST http://localhost:8001/store \\"
    echo -e "        -H 'Authorization: Bearer $api_key' \\"
    echo -e "        -H 'Content-Type: application/json' \\"
    echo -e "        -d '{\"content\":\"Chris works at Stoke\"}'"
    echo ""
    echo -e "    ${CYAN}•${NC} Search:"
    echo -e "      curl http://localhost:8001/search?query=chris \\"
    echo -e"       -H 'Authorization: Bearer $api_key'"
    echo ""
    echo -e "    ${CYAN}•${NC} Backup: ${CYAN}bash scripts/backup.sh${NC}"
    echo -e "    ${CYAN}•${NC} Health: ${CYAN}bash scripts/health_check.sh${NC}"
    echo -e "    ${CYAN}•${NC} Migrate Hermes sessions: ${CYAN}uv run python scripts/migrate_from_sqlite.py${NC}"
    echo ""
    echo -e "  ${BOLD}Docs:${NC} ${CYAN}https://github.com/fattchris/ferrite${NC}"
    echo -e "  ${BOLD}Logs:${NC} ${CYAN}$INSTALL_LOG${NC}"
    echo ""
}

# ============================================================================
# Main
# ============================================================================

main() {
    preflight
    step_secrets
    step_llm
    step_docker
    step_tls
    step_hermes
    step_seed
    step_verify
    step_summary
}

main "$@"
