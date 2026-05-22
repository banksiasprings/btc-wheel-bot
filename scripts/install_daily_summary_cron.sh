#!/bin/bash
# install_daily_summary_cron.sh — Wire up the 8am AEST daily summary push
# Run once:  bash scripts/install_daily_summary_cron.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PUSH_SCRIPT="$SCRIPT_DIR/daily_summary_push.sh"
chmod +x "$PUSH_SCRIPT"

# 8am AEST = UTC+10 = 22:00 UTC (previous day)
# Cron expression: minute hour dom month dow
CRON_LINE="0 22 * * * $PUSH_SCRIPT"

# Check if already installed
if crontab -l 2>/dev/null | grep -qF "$PUSH_SCRIPT"; then
  echo "✅ Cron job already installed."
else
  # Append to existing crontab (or create new)
  ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -
  echo "✅ Cron job installed: $CRON_LINE"
fi

echo ""
echo "The daily summary will be pushed to ntfy.sh topic 'bsf-voice-tasks' at 8:00 AM AEST every day."
echo "To test immediately: bash $PUSH_SCRIPT"
echo "To preview the message: curl http://127.0.0.1:8765/notify/daily-summary"
