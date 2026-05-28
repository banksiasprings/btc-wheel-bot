#!/bin/bash
# Stage 1 extended run — 50M steps, resumes from 5M model
# Waits for the current 5M run to finish first
set -euo pipefail
cd "$(dirname "$0")"

echo "[$(date)] Waiting for 5M run (PID 4671) to finish..."
while kill -0 4671 2>/dev/null; do
    sleep 30
done
echo "[$(date)] 5M run complete. Starting 50M continuation..."

caffeinate -dims python3.11 train_curriculum.py \
    --timesteps 50000000 \
    --checkpoint-freq 1000000 \
    --resume checkpoints/curriculum-s1/curriculum_s1_final.zip \
    2>&1 | tee -a curriculum_s1_50M.log

echo "[$(date)] 50M run complete."
