#!/bin/bash
# Starts both the bot and the FastAPI mobile server together.
# Run this from the repo root or any directory — it resolves paths automatically.
#
# Usage:
#   ./scripts/start_bot_with_api.sh [--mode paper|testnet|live]

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="/usr/local/bin/python3.11"
MODE="${1:---mode paper}"

cd "$REPO"

echo "Starting FastAPI server on :8765…"
"$PYTHON" -m uvicorn api:app --host 0.0.0.0 --port 8765 &
API_PID=$!
echo "  API PID: $API_PID"

echo "Starting bot ($MODE)…"
"$PYTHON" main.py $MODE &
BOT_PID=$!
echo "  Bot PID: $BOT_PID"

echo ""
echo "Both processes running. Press Ctrl+C to stop both."

cleanup() {
  echo "Stopping API (PID $API_PID) and bot (PID $BOT_PID)…"
  kill "$API_PID" "$BOT_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait "$API_PID" "$BOT_PID"
