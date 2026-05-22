#!/bin/bash
# daily_summary_push.sh — Push daily bot farm summary to ntfy.sh
# Scheduled at 8:00 AM AEST (UTC+10) = 22:00 UTC previous day
# Install via: crontab -e  →  0 22 * * * /path/to/daily_summary_push.sh
# Or add as a LaunchAgent (see below)

set -euo pipefail

LOG_DIR="$(dirname "$0")/../logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily_summary.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Pushing daily summary..." >> "$LOG"

# Hit the bot farm API endpoint (localhost — no auth needed for this route)
RESPONSE=$(curl -s -X POST http://127.0.0.1:8765/notify/daily-summary \
  -H "Content-Type: application/json" \
  --max-time 15 2>&1) || true

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Response: $RESPONSE" >> "$LOG"
