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

# ── Grid farm plist (runs grid_farm.py — writes grid_farm/status.json hourly) ──
cat > "$PLIST_DIR/com.wheelbot.gridfarm.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.wheelbot.gridfarm</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3.11</string>
        <string>grid_farm.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/wheelbot_gridfarm.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/wheelbot_gridfarm.log</string>
</dict>
</plist>
EOF

# ── Daily Telegram summary (fires once a day at 8am local) ──
cat > "$PLIST_DIR/com.wheelbot.dailysummary.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.wheelbot.dailysummary</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3.11</string>
        <string>telegram_summary.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>8</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/wheelbot_dailysummary.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/wheelbot_dailysummary.log</string>
</dict>
</plist>
EOF

# Load all agents now (don't wait for reboot)
for svc in api tunnel gridfarm dailysummary; do
  launchctl unload "$PLIST_DIR/com.wheelbot.$svc.plist" 2>/dev/null || true
  launchctl load "$PLIST_DIR/com.wheelbot.$svc.plist"
done

echo "✅ Grid-farm API, tunnel, farm, and daily Telegram summary installed."
echo "   They start automatically every time you log in / reboot."
echo "   Logs: /tmp/wheelbot_*.log"
