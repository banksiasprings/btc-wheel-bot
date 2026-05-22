#!/bin/bash
# Launch training v2 (2M steps, capital-efficiency reward)
cd /Users/openclaw/Documents/btc-wheel-bot/rl_agent
/usr/local/bin/python3.11 train.py \
    --timesteps 2000000 \
    --checkpoint-freq 100000 \
    --data data/btc_daily.csv \
    >> training_v2.log 2>&1
