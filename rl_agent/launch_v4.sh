#!/bin/bash
# V4: SAC + Max ROI + Survival reward
# Evolution pipeline Phase 3 — align reward with Steven's goal
# ~68 hours for 20M steps on Mac CPU
set -euo pipefail
cd "$(dirname "$0")"

echo "=== V4 Training — Max ROI + Survival ==="
echo "Started: $(date)"
echo "Goal: Maximum annualised ROI with survival instinct"
echo "Reward: uncapped ROI + stepped survival (10/20/30% DD thresholds)"
echo "Algorithm: SAC (target_entropy=-2.0)"
echo ""

caffeinate -dims python3.11 train_v4.py \
    --timesteps 20000000 \
    --checkpoint-freq 1000000 \
    2>&1 | tee -a v4_training.log

echo ""
echo "=== V4 complete at $(date) ==="
