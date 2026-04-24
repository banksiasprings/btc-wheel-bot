#!/bin/bash
# start_tunnel.sh — Start ngrok tunnel for Wheel Bot API
# Uses a permanent static domain — URL never changes on restart.
#
# Requires ngrok: brew install ngrok
# Authtoken must be configured: ngrok config add-authtoken <token>

LOG="/tmp/wheel_tunnel.log"

echo "[$(date)] Starting Wheel Bot ngrok tunnel..." > "$LOG"
echo "[$(date)] Tunnel URL (permanent): https://divorcee-quickness-ravine.ngrok-free.dev" >> "$LOG"

exec /usr/local/bin/ngrok http \
    --url=divorcee-quickness-ravine.ngrok-free.dev \
    --log=stdout \
    8765
