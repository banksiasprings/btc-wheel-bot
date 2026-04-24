#!/bin/bash
# install_launchd.sh — Install Wheel Bot API + tunnel as Mac login items
# Run once: ./scripts/install_launchd.sh
# After this, the bot API and tunnel start automatically on login.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$PLIST_DIR"

# ── API server plist ──────────────────────────────────────────────────────────
cat > "$PLIST_DIR/com.wheelbot.api.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.wheelbot.api</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3.11</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>api:app</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>8765</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/wheelbot_api.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/wheelbot_api.log</string>
</dict>
</plist>
EOF

# ── Tunnel plist ──────────────────────────────────────────────────────────────
cat > "$PLIST_DIR/com.wheelbot.tunnel.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.wheelbot.tunnel</string>
    <key>ProgramArguments</key>
    <array>
        <string>$REPO/scripts/start_tunnel.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/wheelbot_tunnel.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/wheelbot_tunnel.log</string>
</dict>
</plist>
EOF

# Load both agents now (don't wait for reboot)
launchctl unload "$PLIST_DIR/com.wheelbot.api.plist" 2>/dev/null || true
launchctl unload "$PLIST_DIR/com.wheelbot.tunnel.plist" 2>/dev/null || true
launchctl load "$PLIST_DIR/com.wheelbot.api.plist"
launchctl load "$PLIST_DIR/com.wheelbot.tunnel.plist"

echo "✅ Wheel Bot API and tunnel installed as login services."
echo "   They will start automatically every time you log in."
echo "   Logs: /tmp/wheelbot_api.log and /tmp/wheelbot_tunnel.log"
