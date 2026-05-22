#!/bin/bash
LOG=~/Documents/btc-wheel-bot/mac-health-log.txt
mkdir -p ~/Documents/btc-wheel-bot
{
  date '+[HEALTH %Y-%m-%d %H:%M]'
  echo "Uptime: $(uptime)"
  echo 'Top CPU processes:'
  top -l 1 -n 5 -stats command,cpu,mem -o cpu | tail -n 6 | sed 's/^/  /'
  PAGES_FREE=$(vm_stat | awk '/Pages free/ {gsub(/\./,"",$3); print $3}')
  FREE_MB=$((PAGES_FREE * 4096 / 1024 / 1024))
  PAGES_INACTIVE=$(vm_stat | awk '/Pages inactive/ {gsub(/\./,"",$3); print $3}')
  INACTIVE_MB=$((PAGES_INACTIVE * 4096 / 1024 / 1024))
  echo "Memory: ${FREE_MB}MB free, ${INACTIVE_MB}MB inactive"
  echo "Disk: $(df -h / | tail -1 | awk '{print $3 " used / " $4 " available"}')"
  THERM=$(pmset -g thermlog 2>/dev/null | tail -1)
  if [ -z "$THERM" ]; then THERM='N/A'; fi
  echo "Thermal: $THERM"
  echo ''
} >> $LOG
echo done
