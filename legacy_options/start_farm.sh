#!/bin/bash
# Start the bot farm using the correct Python (3.11 with all deps installed)
cd /Users/openclaw/Documents/btc-wheel-bot

# Ensure data dir exists for PID file
mkdir -p data

nohup /usr/local/bin/python3.11 bot_farm.py >> logs/farm.log 2>&1 &
FARM_PID=$!

# Write PID to both locations: api.py checks data/farm_pid.txt
echo $FARM_PID > data/farm_pid.txt
echo $FARM_PID > /tmp/farm.pid

echo "Farm started PID: $FARM_PID"
