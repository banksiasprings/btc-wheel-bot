#!/bin/bash
# Start the bot farm using the correct Python (3.11 with all deps installed)
cd /Users/openclaw/Documents/btc-wheel-bot
nohup /usr/local/bin/python3.11 bot_farm.py >> logs/farm.log 2>&1 &
FARM_PID=$!
echo $FARM_PID > /tmp/farm.pid
echo "Farm started PID: $FARM_PID"
