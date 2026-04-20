#!/usr/bin/env bash
# start_bot_with_api.sh — Start the FastAPI server and the wheel bot together.
#
# Usage:
#   bash scripts/start_bot_with_api.sh            # paper mode (default)
#   bash scripts/start_bot_with_api.sh --live      # live mode
#
# Both processes run in the background. PIDs are printed so you can kill them.
# Logs: logs/api.log  and  logs/bot.log

set -e

cd "$(dirname "$0")/.."

PYTHON=/usr/local/bin/python3.11
PORT=${WHEEL_API_PORT:-8765}
MODE_FLAG=""
if [[ "$1" == "--live" ]]; then
  MODE_FLAG="--live"
fi

mkdir -p logs

echo "[start] Launching API server on port ${PORT}…"
$PYTHON -m uvicorn api:app --host 0.0.0.0 --port "$PORT" \
  >> logs/api.log 2>&1 &
API_PID=$!
echo "[start] API PID: $API_PID"

sleep 1   # give uvicorn a moment to bind

echo "[start] Launching wheel bot (${MODE_FLAG:-paper mode})…"
$PYTHON main.py $MODE_FLAG >> logs/bot.log 2>&1 &
BOT_PID=$!
echo "[start] Bot PID: $BOT_PID"

echo ""
echo "  API log:  logs/api.log"
echo "  Bot log:  logs/bot.log"
echo ""
echo "  To stop both processes:"
echo "    kill $API_PID $BOT_PID"
echo ""
echo "  Or run:  bash scripts/start_tunnel.sh   to expose via Cloudflare"
