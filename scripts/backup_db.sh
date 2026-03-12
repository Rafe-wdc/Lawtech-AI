#!/bin/bash
# Lawtech-AI — SQLite Daily Backup
#
# Usage:
#   ./scripts/backup_db.sh
#
# Cron (runs at 2 AM daily):
#   0 2 * * * /path/to/Lawtech-AI/scripts/backup_db.sh >> /path/to/Lawtech-AI/logs/backup.log 2>&1
#
# What it does:
#   1. Hot-backup the SQLite DB using sqlite3's .backup command (safe while DB is live)
#   2. Compresses the backup with gzip
#   3. Deletes backups older than 7 days (keeps last 7)
#   4. Prints a one-line status with file size

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DB_PATH="$PROJECT_ROOT/data/chat_history.db"
BACKUP_DIR="$PROJECT_ROOT/data/backups"
DATE="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/chat_history_$DATE.db"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Check DB exists
if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: Database not found at $DB_PATH"
    exit 1
fi

# Hot backup using sqlite3 .backup (safe while app is running)
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

# Compress
gzip -f "$BACKUP_FILE"
COMPRESSED="${BACKUP_FILE}.gz"

# Report size
SIZE=$(du -sh "$COMPRESSED" | cut -f1)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup created: $(basename "$COMPRESSED") ($SIZE)"

# Keep last 7 days only
find "$BACKUP_DIR" -name "chat_history_*.db.gz" -mtime +7 -delete
REMAINING=$(find "$BACKUP_DIR" -name "chat_history_*.db.gz" | wc -l)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backups retained: $REMAINING"
