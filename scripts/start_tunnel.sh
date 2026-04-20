#!/bin/bash
# start_tunnel.sh — Start Cloudflare quick tunnel for Wheel Bot API
# Captures the tunnel URL and emails it to smcnichol@outlook.com so
# Steven always knows the current URL without touching the Mac.
#
# Requires cloudflared: brew install cloudflared

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="/tmp/wheel_tunnel.log"
API_KEY=$(grep WHEEL_API_KEY "$REPO/.env" 2>/dev/null | cut -d= -f2 || echo "")
EMAIL_SENT=0

echo "[$(date)] Starting Wheel Bot tunnel..." > "$LOG"
echo "Starting Cloudflare tunnel → http://localhost:8765"
echo "A URL email will be sent to smcnichol@outlook.com once connected."
echo ""

# Start tunnel, tee output so it shows in terminal AND we can scan for URL
/usr/local/bin/cloudflared tunnel --url http://localhost:8765 2>&1 | tee -a "$LOG" | while IFS= read -r line; do
    echo "$line"

    # Detect the trycloudflare URL once (first match only)
    if [[ $EMAIL_SENT -eq 0 && "$line" =~ https://[a-zA-Z0-9-]+\.trycloudflare\.com ]]; then
        TUNNEL_URL="${BASH_REMATCH[0]}"
        EMAIL_SENT=1
        echo "[$(date)] Tunnel URL: $TUNNEL_URL — sending email..." >> "$LOG"

        # Send via Mail.app (works without SMTP config)
        osascript <<APPLESCRIPT 2>/dev/null
tell application "Mail"
    set msg to make new outgoing message with properties {¬
        subject:"🤖 Wheel Bot is running — new URL", ¬
        content:"Your Wheel Bot is online.\n\nOpen the Wheel Bot app and go to Settings → API URL and paste this:\n\n$TUNNEL_URL\n\nYour API key hasn't changed:\n$API_KEY\n\nThis URL is valid until the Mac restarts. You'll get a new email automatically next time.", ¬
        visible:false}
    tell msg
        make new to recipient with properties {address:"smcnichol@outlook.com"}
    end tell
    send msg
end tell
APPLESCRIPT

        echo "[$(date)] Email sent." >> "$LOG"
    fi
done
