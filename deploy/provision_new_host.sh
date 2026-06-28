#!/usr/bin/env bash
# =============================================================================
# Lawtech-AI — Phase 1 provisioning script for a fresh Ubuntu 24.04 LTS host.
#
# Runs idempotently — re-run after a failure and it picks up where it left off.
# This script does NOT touch the existing prod box; it only bootstraps the new
# scale-target host (8 vCPU / 32 GB / NVMe).
#
# Pre-requisites:
#   • Fresh Ubuntu 24.04 LTS VM, root access (or sudo).
#   • This Lawtech-AI repo already rsynced to /root/Lawtech-AI/.
#   • Embedding models present at /root/Lawtech-AI/models/ (BGE-large + MiniLM).
#   • /root/Lawtech-AI/.env populated from .env.production template.
#
# Usage:
#   sudo bash /root/Lawtech-AI/deploy/provision_new_host.sh
#
# After this completes successfully, the services are NOT yet started — that
# happens in Phase 2 (gunicorn cutover). See the runbook for the next step.
# =============================================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_DIR="${PROJECT_DIR:-/root/Lawtech-AI}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/venv}"
PG_VERSION="${PG_VERSION:-16}"
PG_DB="${PG_DB:-lawtech}"
PG_USER="${PG_USER:-lawtech}"

# Colours
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; B='\033[1m'; N='\033[0m'
info()  { echo -e "${G}[INFO]${N}  $*"; }
warn()  { echo -e "${Y}[WARN]${N}  $*"; }
fatal() { echo -e "${R}[FATAL]${N} $*"; exit 1; }
section() { echo -e "\n${B}=== $* ===${N}"; }

# ── Sanity ────────────────────────────────────────────────────────────────────
section "Pre-flight"
[[ $EUID -eq 0 ]] || fatal "Must run as root (sudo)."
[[ -d "$PROJECT_DIR" ]] || fatal "$PROJECT_DIR not found — rsync the repo first."
[[ -f "$PROJECT_DIR/.env" ]] || fatal "$PROJECT_DIR/.env not found — copy from .env.production."

OS_VERSION=$(lsb_release -rs 2>/dev/null || echo "unknown")
[[ "$OS_VERSION" == "24.04" ]] || warn "Expected Ubuntu 24.04, got $OS_VERSION — continuing anyway."

info "Project dir: $PROJECT_DIR"
info "OS: Ubuntu $OS_VERSION"

# ── Base packages ─────────────────────────────────────────────────────────────
section "APT packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -yqq \
    python3.12 python3.12-venv python3.12-dev python3-pip \
    build-essential pkg-config \
    nginx logrotate certbot python3-certbot-nginx \
    git curl wget rsync htop ncdu jq \
    "postgresql-$PG_VERSION" "postgresql-contrib-$PG_VERSION" \
    redis-server \
    libpq-dev libssl-dev \
    fail2ban ufw

info "APT packages installed."

# ── Kernel / sysctl tuning ───────────────────────────────────────────────────
section "Kernel tuning (high-concurrency uvicorn)"
cat > /etc/sysctl.d/99-lawtech.conf <<'EOF'
# Lawtech-AI — high-concurrency tuning for 50 in-flight SSE streams.
# Increases the socket-accept queue and file-descriptor headroom.
net.core.somaxconn         = 4096
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_max_syn_backlog = 4096
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse      = 1
net.ipv4.tcp_fin_timeout   = 15
fs.file-max                = 2097152
vm.overcommit_memory       = 1
EOF
sysctl --system >/dev/null
info "sysctl applied."

# Per-user fd limit (systemd LimitNOFILE handles services; this is for shells)
if ! grep -q "lawtech-fd-limit" /etc/security/limits.d/99-lawtech.conf 2>/dev/null; then
    cat > /etc/security/limits.d/99-lawtech.conf <<'EOF'
# lawtech-fd-limit
*   soft  nofile  65536
*   hard  nofile  65536
root soft nofile  65536
root hard nofile  65536
EOF
fi
info "limits.d configured."

# ── Firewall ──────────────────────────────────────────────────────────────────
section "Firewall (ufw)"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP (Certbot + nginx)'
ufw allow 443/tcp comment 'HTTPS'
# Internal-only ports are bound to 127.0.0.1; do NOT open them.
ufw --force enable
info "ufw enabled."

# ── PostgreSQL ────────────────────────────────────────────────────────────────
section "PostgreSQL $PG_VERSION"
systemctl enable --now "postgresql"

# Create DB + user idempotently.
PG_PW_FILE="/root/.lawtech-pg.pw"
if [[ ! -f "$PG_PW_FILE" ]]; then
    head -c 32 /dev/urandom | base64 | tr -d '/+=' > "$PG_PW_FILE"
    chmod 600 "$PG_PW_FILE"
    info "Generated Postgres password → $PG_PW_FILE"
else
    info "Reusing existing Postgres password from $PG_PW_FILE"
fi
PG_PW="$(cat "$PG_PW_FILE")"

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$PG_USER';" | grep -q 1; then
    sudo -u postgres psql -c "CREATE USER $PG_USER WITH PASSWORD '$PG_PW';" >/dev/null
    info "Created PG user $PG_USER."
else
    sudo -u postgres psql -c "ALTER USER $PG_USER WITH PASSWORD '$PG_PW';" >/dev/null
    info "Updated PG user $PG_USER password."
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB';" | grep -q 1; then
    sudo -u postgres createdb -O "$PG_USER" "$PG_DB"
    info "Created PG database $PG_DB."
else
    info "PG database $PG_DB already exists."
fi

# Drop tuning config in place.
PG_CONF_DIR="/etc/postgresql/$PG_VERSION/main/conf.d"
mkdir -p "$PG_CONF_DIR"
cp "$PROJECT_DIR/deploy/postgresql.lawtech.conf" "$PG_CONF_DIR/lawtech.conf"
info "Installed tuning config at $PG_CONF_DIR/lawtech.conf"

systemctl restart postgresql
info "PostgreSQL restarted with new config."

# Sanity-print the tuned values.
echo -n "  shared_buffers       = "; sudo -u postgres psql -tAc "SHOW shared_buffers;"
echo -n "  effective_cache_size = "; sudo -u postgres psql -tAc "SHOW effective_cache_size;"
echo -n "  max_connections      = "; sudo -u postgres psql -tAc "SHOW max_connections;"

# Patch .env with the new POSTGRES_URL if it still has placeholder.
if grep -q "CHANGE_ME" "$PROJECT_DIR/.env" 2>/dev/null; then
    warn ".env still has CHANGE_ME placeholders — patching POSTGRES_URL only."
    sed -i "s|postgresql://lawtech:CHANGE_ME@localhost:5432/lawtech|postgresql://$PG_USER:$PG_PW@localhost:5432/$PG_DB|g" "$PROJECT_DIR/.env"
fi

# ── Python venv + deps ────────────────────────────────────────────────────────
section "Python virtualenv + dependencies"
if [[ ! -d "$VENV_DIR" ]]; then
    python3.12 -m venv "$VENV_DIR"
    info "Created venv at $VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip

if [[ -f "$PROJECT_DIR/requirements.lock.txt" ]]; then
    info "Installing pinned dependencies from requirements.lock.txt..."
    "$VENV_DIR/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.lock.txt"
else
    warn "requirements.lock.txt not found — falling back to requirements.txt (UNPINNED, NOT FOR PROD)"
    "$VENV_DIR/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.txt"
fi
info "Python deps installed."

# Gunicorn (not in requirements by default — needed for multi-worker prod).
"$VENV_DIR/bin/pip" install --quiet "gunicorn>=21.2.0" "uvloop>=0.19.0" "httptools>=0.6.0"
info "Gunicorn + uvloop + httptools installed."

# Sanity check: import the app to surface any startup errors before we wire systemd.
info "Smoke-importing the FastAPI app..."
( cd "$PROJECT_DIR" && "$VENV_DIR/bin/python" -c "from core.gateway import app; print('OK — app imported.')" )

# ── Models check ──────────────────────────────────────────────────────────────
section "Embedding models"
for m in "bge-large-en-v1.5" "all-MiniLM-L6-v2"; do
    if [[ -d "$PROJECT_DIR/models/$m" ]]; then
        info "Found $m"
    else
        warn "Missing $PROJECT_DIR/models/$m — copy it from the old box."
    fi
done

# ── Directories ───────────────────────────────────────────────────────────────
section "Runtime dirs"
mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data" "$PROJECT_DIR/chroma_store" "$PROJECT_DIR/uploads"
chmod 700 "$PROJECT_DIR/.env"
info "Runtime dirs ready."

# ── systemd units ─────────────────────────────────────────────────────────────
section "systemd units"
for unit in lawttorney-v2.service lawttorney-embed.service lawttorney-chroma.service; do
    if [[ -f "$PROJECT_DIR/deploy/systemd/$unit" ]]; then
        cp "$PROJECT_DIR/deploy/systemd/$unit" "/etc/systemd/system/$unit"
        info "Installed $unit"
    else
        warn "Missing $PROJECT_DIR/deploy/systemd/$unit — skipping."
    fi
done
systemctl daemon-reload

# Embeddings are loaded in-process by the API (default). The embed unit
# file is installed but NOT enabled — opt in only if you need to scale
# embedding throughput beyond a single Python process.
# Do NOT auto-start lawttorney-v2 yet — that happens in Phase 2 after
# Phase 1.6 smoke test confirms the box is healthy.
# Do NOT enable lawttorney-chroma — that's Phase 5.
info "lawttorney-embed installed but NOT enabled — in-process embeddings are the default."
info "lawttorney-v2 installed but NOT enabled — Phase 2 controls cutover."
info "lawttorney-chroma installed but NOT enabled — Phase 5 controls cutover."

# ── logrotate ─────────────────────────────────────────────────────────────────
section "logrotate"
if [[ -f "$PROJECT_DIR/deploy/logrotate.conf" ]]; then
    cp "$PROJECT_DIR/deploy/logrotate.conf" /etc/logrotate.d/lawtech-ai
    chmod 644 /etc/logrotate.d/lawtech-ai
    logrotate --debug /etc/logrotate.d/lawtech-ai >/dev/null
    info "logrotate installed and dry-run passed."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
section "Provisioning complete"
echo ""
echo "  Next steps:"
echo "    1. Smoke-test single worker:   ./start.sh   (uvicorn — loads BGE + MiniLM in-process)"
echo "    2. Run runbook §6 verification (curl /pyapi/health, /pyapi/health/detailed)"
echo "    3. When ready: switch to gunicorn (Phase 2)"
echo "    4. (Optional) Remote embeddings: systemctl enable --now lawttorney-embed,"
echo "       then set EMBEDDING_SERVICE_URL=http://localhost:5100 in .env, restart lawttorney-v2."
echo ""
echo "  Postgres password is at $PG_PW_FILE (chmod 600)."
echo "  Tail API logs:  journalctl -u lawttorney-v2 -f"
echo ""
