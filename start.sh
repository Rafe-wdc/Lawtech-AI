#!/usr/bin/env bash
# =============================================================================
# Lawttorney v2 — Multi-Agent System Startup Script
# =============================================================================
# Usage:
#   ./start.sh               — start main API server (default)
#   ./start.sh --with-embed  — start embedding service first, then API server
#   ./start.sh --embed-only  — start embedding service only
#   ./start.sh --stop        — kill all running Lawttorney processes
#   ./start.sh --status      — show running process status
# =============================================================================

set -euo pipefail

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
V2_DIR="$SCRIPT_DIR"
LOG_DIR="$V2_DIR/logs"
PID_DIR="$V2_DIR/logs"

API_PID_FILE="$PID_DIR/api.pid"
EMBED_PID_FILE="$PID_DIR/embed.pid"
API_LOG="$LOG_DIR/api.log"
EMBED_LOG="$LOG_DIR/embed.log"

# ── Config (override via environment or .env) ─────────────────────────────────
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"
EMBED_PORT="${EMBED_PORT:-5100}"
WORKERS="${WORKERS:-1}"
LOG_LEVEL="${LOG_LEVEL:-info}"
RELOAD="${RELOAD:-false}"

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Helpers ───────────────────────────────────────────────────────────────────
log_info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }
log_section() { echo -e "\n${BOLD}${CYAN}=== $* ===${NC}"; }

banner() {
    echo -e "${BOLD}${CYAN}"
    echo "  ██╗      █████╗ ██╗    ██╗████████╗████████╗ ██████╗ ██████╗ ███╗   ██╗███████╗██╗   ██╗"
    echo "  ██║     ██╔══██╗██║    ██║╚══██╔══╝╚══██╔══╝██╔═══██╗██╔══██╗████╗  ██║██╔════╝╚██╗ ██╔╝"
    echo "  ██║     ███████║██║ █╗ ██║   ██║      ██║   ██║   ██║██████╔╝██╔██╗ ██║█████╗   ╚████╔╝ "
    echo "  ██║     ██╔══██║██║███╗██║   ██║      ██║   ██║   ██║██╔══██╗██║╚██╗██║██╔══╝    ╚██╔╝  "
    echo "  ███████╗██║  ██║╚███╔███╔╝   ██║      ██║   ╚██████╔╝██║  ██║██║ ╚████║███████╗   ██║   "
    echo "  ╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝    ╚═╝      ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝   ╚═╝  "
    echo -e "${NC}${BOLD}  Lawttorney v2 — Multi-Agent Legal AI System${NC}"
    echo "  ─────────────────────────────────────────────"
}

# ── Prerequisite checks ───────────────────────────────────────────────────────
check_prerequisites() {
    log_section "Prerequisites"

    # Python
    if ! command -v python &>/dev/null; then
        log_error "Python not found. Please install Python 3.10+."
        exit 1
    fi
    PYTHON_VER=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    log_info "Python $PYTHON_VER found"

    # .env file
    if [[ -f "$V2_DIR/.env" ]]; then
        log_info ".env loaded from $V2_DIR/.env"
        set -a; source "$V2_DIR/.env"; set +a
    elif [[ -f "$PROJECT_ROOT/.env" ]]; then
        log_info ".env loaded from $PROJECT_ROOT/.env"
        set -a; source "$PROJECT_ROOT/.env"; set +a
    else
        log_warn "No .env file found — relying on system environment variables"
    fi

    # Required API keys
    local missing=()
    [[ -z "${OPENAI_API_KEY:-}" ]] && missing+=("OPENAI_API_KEY")
    [[ -z "${GOOGLE_API_KEY:-}" ]] && missing+=("GOOGLE_API_KEY")
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required environment variables: ${missing[*]}"
        log_error "Set them in $V2_DIR/.env or export them before running."
        exit 1
    fi
    log_info "API keys verified (OpenAI, Google)"

    # Log directory
    mkdir -p "$LOG_DIR"
    log_info "Log directory: $LOG_DIR"
}

# ── Port check ────────────────────────────────────────────────────────────────
check_port() {
    local port="$1"
    local name="$2"
    # Try netstat (Windows/Linux) or ss (Linux)
    if netstat -ano 2>/dev/null | grep -q ":$port "; then
        log_warn "Port $port ($name) is already in use"
        return 1
    fi
    return 0
}

is_process_alive() {
    local pid="$1"
    kill -0 "$pid" 2>/dev/null
}

# ── Start embedding service ───────────────────────────────────────────────────
start_embedding_service() {
    log_section "Embedding Service (port $EMBED_PORT)"

    if [[ -f "$EMBED_PID_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$EMBED_PID_FILE")
        if is_process_alive "$old_pid"; then
            log_info "Embedding service already running (PID $old_pid)"
            return 0
        fi
        rm -f "$EMBED_PID_FILE"
    fi

    log_info "Starting embedding service..."
    cd "$PROJECT_ROOT"
    EMBEDDING_SERVICE_PORT="$EMBED_PORT" \
    PYTHONPATH="$V2_DIR" \
    python -m services.embedding_service \
        >> "$EMBED_LOG" 2>&1 &

    local pid=$!
    echo "$pid" > "$EMBED_PID_FILE"
    log_info "Embedding service started (PID $pid) — log: $EMBED_LOG"

    # Wait for health check (up to 30s)
    log_info "Waiting for embedding service to be ready..."
    local attempts=0
    until curl -sf "http://localhost:$EMBED_PORT/health" &>/dev/null; do
        sleep 1
        ((attempts++))
        if [[ $attempts -ge 30 ]]; then
            log_error "Embedding service failed to start within 30 seconds"
            log_error "Check log: $EMBED_LOG"
            exit 1
        fi
        [[ $((attempts % 5)) -eq 0 ]] && log_info "  still waiting... (${attempts}s)"
    done
    log_info "Embedding service is ready"

    # Export URL so the API server uses it
    export EMBEDDING_SERVICE_URL="http://localhost:$EMBED_PORT"
}

# ── Start API server ──────────────────────────────────────────────────────────
start_api_server() {
    log_section "API Server (port $PORT)"

    if [[ -f "$API_PID_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$API_PID_FILE")
        if is_process_alive "$old_pid"; then
            log_info "API server already running (PID $old_pid)"
            log_info "  Use './start.sh --stop' to stop it first."
            return 0
        fi
        rm -f "$API_PID_FILE"
    fi

    if ! check_port "$PORT" "API"; then
        log_warn "Another process is using port $PORT."
        log_warn "Run './start.sh --stop' or kill the process manually."
        exit 1
    fi

    log_info "Starting API server..."

    # Build uvicorn args
    local uvicorn_args=(
        "core.gateway:app"
        "--host" "$HOST"
        "--port" "$PORT"
        "--log-level" "$LOG_LEVEL"
        "--timeout-keep-alive" "120"
    )

    # Reload mode (dev only, single worker)
    if [[ "$RELOAD" == "true" ]]; then
        uvicorn_args+=("--reload")
        log_warn "Hot-reload enabled (development mode, single worker)"
    else
        uvicorn_args+=("--workers" "$WORKERS")
    fi

    cd "$V2_DIR"
    python -m uvicorn "${uvicorn_args[@]}" \
        >> "$API_LOG" 2>&1 &

    local pid=$!
    echo "$pid" > "$API_PID_FILE"
    log_info "API server started (PID $pid) — log: $API_LOG"

    # Wait for health check (up to 30s)
    log_info "Waiting for API server to be ready..."
    local attempts=0
    until curl -sf "http://localhost:$PORT/pyapi/health" &>/dev/null; do
        sleep 1
        ((attempts++))
        if [[ $attempts -ge 30 ]]; then
            log_error "API server failed to start within 30 seconds"
            log_error "Check log: $API_LOG"
            cat "$API_LOG" | tail -30
            exit 1
        fi
        [[ $((attempts % 5)) -eq 0 ]] && log_info "  still waiting... (${attempts}s)"
    done
    log_info "API server is ready"
}

# ── Stop all processes ────────────────────────────────────────────────────────
stop_all() {
    log_section "Stopping Lawttorney"
    local stopped=0

    for pid_file in "$API_PID_FILE" "$EMBED_PID_FILE"; do
        if [[ -f "$pid_file" ]]; then
            local pid name
            pid=$(cat "$pid_file")
            name=$(basename "$pid_file" .pid)
            if is_process_alive "$pid"; then
                log_info "Stopping $name (PID $pid)..."
                kill "$pid" 2>/dev/null || true
                sleep 2
                if is_process_alive "$pid"; then
                    log_warn "Graceful stop failed, force-killing $name (PID $pid)..."
                    kill -9 "$pid" 2>/dev/null || true
                fi
                log_info "$name stopped"
                ((stopped++))
            else
                log_warn "$name PID $pid not running (stale PID file removed)"
            fi
            rm -f "$pid_file"
        fi
    done

    if [[ $stopped -eq 0 ]]; then
        log_info "No running Lawttorney processes found"
    else
        log_info "Stopped $stopped process(es)"
    fi
}

# ── Status ────────────────────────────────────────────────────────────────────
show_status() {
    log_section "Status"
    local any=false

    for pid_file in "$API_PID_FILE" "$EMBED_PID_FILE"; do
        local name
        name=$(basename "$pid_file" .pid)
        if [[ -f "$pid_file" ]]; then
            local pid
            pid=$(cat "$pid_file")
            if is_process_alive "$pid"; then
                log_info "${BOLD}$name${NC}: ${GREEN}RUNNING${NC} (PID $pid)"
                any=true
            else
                log_warn "${BOLD}$name${NC}: ${RED}DEAD${NC} (stale PID $pid)"
            fi
        else
            echo -e "  ${BOLD}$name${NC}: ${YELLOW}NOT STARTED${NC}"
        fi
    done

    echo ""
    # Quick health checks
    if curl -sf "http://localhost:$PORT/pyapi/health" &>/dev/null; then
        log_info "API health: ${GREEN}OK${NC} → http://localhost:$PORT/pyapi/health"
    else
        log_warn "API health: ${RED}UNREACHABLE${NC} (port $PORT)"
    fi

    if curl -sf "http://localhost:$EMBED_PORT/health" &>/dev/null; then
        log_info "Embedding health: ${GREEN}OK${NC} → http://localhost:$EMBED_PORT/health"
    else
        echo -e "  Embedding health: ${YELLOW}NOT RUNNING${NC} (port $EMBED_PORT)"
    fi
}

# ── Print ready summary ───────────────────────────────────────────────────────
print_ready() {
    echo ""
    echo -e "${BOLD}${GREEN}✓ Lawttorney is running!${NC}"
    echo "  ─────────────────────────────────────"
    echo -e "  API:          ${CYAN}http://localhost:$PORT${NC}"
    echo -e "  Health:       ${CYAN}http://localhost:$PORT/pyapi/health${NC}"
    echo -e "  Frontend:     ${CYAN}http://localhost:$PORT/frontend${NC}"
    echo -e "  API Docs:     ${CYAN}http://localhost:$PORT/docs${NC}"
    echo -e "  API Log:      $API_LOG"
    if [[ "${START_EMBED:-false}" == "true" ]]; then
        echo -e "  Embed:        ${CYAN}http://localhost:$EMBED_PORT/health${NC}"
        echo -e "  Embed Log:    $EMBED_LOG"
    fi
    echo "  ─────────────────────────────────────"
    echo -e "  Stop:   ${YELLOW}./start.sh --stop${NC}"
    echo -e "  Status: ${YELLOW}./start.sh --status${NC}"
    echo -e "  Logs:   ${YELLOW}tail -f $API_LOG${NC}"
    echo ""
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    banner

    local mode="api"
    START_EMBED=false

    case "${1:-}" in
        --with-embed)  mode="api";   START_EMBED=true  ;;
        --embed-only)  mode="embed"; START_EMBED=true  ;;
        --stop)        stop_all; exit 0 ;;
        --status)      show_status; exit 0 ;;
        --dev)         RELOAD=true; WORKERS=1; mode="api" ;;
        --help|-h)
            echo "Usage: $0 [--with-embed | --embed-only | --stop | --status | --dev]"
            echo ""
            echo "  (no args)     Start API server only (default)"
            echo "  --with-embed  Start embedding service, then API server"
            echo "  --embed-only  Start embedding service only"
            echo "  --dev         Start API with hot-reload (development)"
            echo "  --stop        Stop all running Lawttorney processes"
            echo "  --status      Show status of all processes"
            exit 0
            ;;
        "")  ;;  # default: api only
        *)
            log_error "Unknown option: $1"
            echo "Run '$0 --help' for usage."
            exit 1
            ;;
    esac

    check_prerequisites

    if [[ "$START_EMBED" == "true" ]]; then
        start_embedding_service
    fi

    if [[ "$mode" != "embed" ]]; then
        start_api_server
    fi

    print_ready
}

main "$@"
