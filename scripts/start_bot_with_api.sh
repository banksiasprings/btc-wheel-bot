#!/bin/bash
# Starts the grid-farm web API + the grid farm together.
# Run this from the repo root or any directory — it resolves paths automatically.
# The API serves bot.banksiaspringsfarm.com (via the cloudflared tunnel) on :8765.
#
# Usage:
#   ./scripts/start_bot_with_api.sh

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="/usr/local/bin/python3.11"

cd "$REPO"

echo "Starting grid-farm API on :8765…"
"$PYTHON" -m uvicorn api:app --host 0.0.0.0 --port 8765 &
API_PID=$!
echo "  API PID: $API_PID"

echo "Starting grid farm (7 paper variants on live prices)…"
"$PYTHON" grid_farm.py &
FARM_PID=$!
echo "  Grid farm PID: $FARM_PID"

echo ""
echo "Both processes running. Press Ctrl+C to stop both."

cleanup() {
  echo "Stopping API (PID $API_PID) and grid farm (PID $FARM_PID)…"
  kill "$API_PID" "$FARM_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait "$API_PID" "$FARM_PID"
