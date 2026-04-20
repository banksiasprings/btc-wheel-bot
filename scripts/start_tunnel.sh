#!/bin/bash
# Starts a Cloudflare Quick Tunnel for the bot API.
# The tunnel URL (e.g. https://xxx.trycloudflare.com) is printed to stdout.
# Copy that URL into the Wheel Bot mobile app on first launch.
#
# Requires cloudflared: brew install cloudflared

set -euo pipefail

if ! command -v cloudflared &>/dev/null; then
  echo "cloudflared not found. Install with: brew install cloudflared" >&2
  exit 1
fi

echo "Starting Cloudflare tunnel → http://localhost:8765"
echo "Copy the https:// URL into the Wheel Bot mobile app."
echo ""
cloudflared tunnel --url http://localhost:8765
