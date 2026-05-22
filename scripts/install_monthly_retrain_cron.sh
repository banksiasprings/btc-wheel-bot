#!/usr/bin/env bash
# Installs a cron job to run monthly_retrain.sh on the 1st of each month at 2am AEST (4pm UTC)
SCRIPT="$HOME/Documents/btc-wheel-bot/scripts/monthly_retrain.sh"
CRON_LINE="0 16 1 * * bash $SCRIPT"
( crontab -l 2>/dev/null | grep -v "monthly_retrain"; echo "$CRON_LINE" ) | crontab -
echo "Installed: $CRON_LINE"
