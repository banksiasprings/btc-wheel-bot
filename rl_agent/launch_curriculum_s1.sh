#!/bin/bash
# Launch Stage 1 curriculum training overnight — 5M steps
# Heston stochastic vol (no jumps), differential Sharpe reward
#
# Usage: bash launch_curriculum_s1.sh
# Logs to: rl_agent/curriculum_s1.log
# Checkpoints to: rl_agent/checkpoints/curriculum-s1/

set -euo pipefail
cd "$(dirname "$0")"

echo "=== BTC Wheel Bot — Curriculum Stage 1 ==="
echo "Starting 5M step training run at $(date)"
echo "Logs: $(pwd)/curriculum_s1.log"
echo "Checkpoints: $(pwd)/checkpoints/curriculum-s1/"
echo ""

# Use caffeinate to prevent Mac from sleeping during training
caffeinate -dims python3.11 train_curriculum.py \
    --timesteps 5000000 \
    --checkpoint-freq 250000 \
    2>&1 | tee -a curriculum_s1.log

echo ""
echo "=== Training complete at $(date) ==="
