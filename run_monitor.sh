#!/usr/bin/env bash
# Invoked by /loop every N hours.
# Reads new log entries, asks Claude to triage them, opens GitHub PRs for bugs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GH_REPO="ShayanShamsi/error-log-monitor-demo"
LOG_FILE="$SCRIPT_DIR/logs/app.log"
MONITOR_DIR="$SCRIPT_DIR/monitor"
MOCK_APP_DIR="$SCRIPT_DIR/mock-app"

echo "=== Error Log Monitor Run: $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="

# 1. Append a fresh batch of simulated logs (simulates N hours of real traffic)
echo "[step 1/3] Generating new log entries..."
cd "$MONITOR_DIR"
uv run python generate_logs.py --hours 2 --out "$LOG_FILE" 2>&1
# Note: generate_logs.py *overwrites* the log each time for demo simplicity;
# in a real setup this would be a live rotating log file.

# Reset cursor so we re-scan the freshly written log
rm -f "$MONITOR_DIR/.log_cursor"

# 2. Analyze logs and open PRs
echo "[step 2/3] Analyzing errors with Claude..."
uv run python analyze.py \
  --log "$LOG_FILE" \
  --repo-dir "$MOCK_APP_DIR" \
  --gh-repo "$GH_REPO" \
  2>&1

echo "[step 3/3] Done."
