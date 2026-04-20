#!/usr/bin/env bash
# start_tunnel.sh — Expose the FastAPI server via Cloudflare Tunnel
# Usage: bash scripts/start_tunnel.sh
#
# Prints the public HTTPS URL — paste it into the mobile app setup screen.

set -e

# Default port matches api.py
PORT=${WHEEL_API_PORT:-8765}

echo "[tunnel] Starting Cloudflare Tunnel → http://localhost:${PORT}"
echo "[tunnel] Copy the printed URL (*.trycloudflare.com) into the mobile app."
echo ""

cloudflared tunnel --url "http://localhost:${PORT}"
