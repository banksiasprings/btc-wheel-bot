#!/bin/bash
LOG="$HOME/Documents/btc-wheel-bot/mac-health-log.txt"
TS=$(date '+%Y-%m-%d %H:%M')
{
  echo "[HEALTH $TS]"
  printf "Uptime: "
  uptime
  echo "Top CPU processes (ps aux, top 5):"
  ps -axco "%cpu,%mem,comm" -r 2>/dev/null | head -6 | tail -5 | sed 's/^/  /'
  echo "Memory:"
  PAGES_FREE=$(vm_stat | awk '/Pages free/ {gsub(/\./,"",$3); print $3}')
  PAGES_INACTIVE=$(vm_stat | awk '/Pages inactive/ {gsub(/\./,"",$3); print $3}')
  if [ -n "$PAGES_FREE" ]; then
    MB_FREE=$(( PAGES_FREE * 4096 / 1024 / 1024 ))
    MB_AVAIL=$(( (PAGES_FREE + PAGES_INACTIVE) * 4096 / 1024 / 1024 ))
    echo "  ${MB_FREE} MB free pages, ${MB_AVAIL} MB free+inactive"
  fi
  MP_LINE=$(memory_pressure 2>/dev/null | grep -i 'free percentage' | head -1)
  [ -n "$MP_LINE" ] && echo "  $MP_LINE"
  echo "Disk:"
  df -h / | tail -1 | awk '{print "  Total:"$2" Used:"$3" Avail:"$4" Cap:"$5}'
  echo "Thermal: N/A (requires root)"
} >> "$LOG" 2>/dev/null
exit 0
