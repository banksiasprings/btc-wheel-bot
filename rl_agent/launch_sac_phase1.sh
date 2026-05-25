#!/bin/bash
# Phase 1: SAC pretrain on Heston — 5M steps
# 16 features (incl IV surface), realistic Deribit costs, DSR reward (eta=0.002)
# ~16 hours on Mac CPU
set -euo pipefail
cd "$(dirname "$0")"

echo "=== SAC Phase 1 — Heston Pretrain ==="
echo "Started: $(date)"
echo "Features: 16 (12 base + VRP, skew, term structure, 30d RV)"
echo "Costs: Deribit realistic (0.03% taker + 2% spread)"
echo "Reward: Differential Sharpe (eta=0.002)"
echo "ETA: ~16 hours"
echo ""

caffeinate -dims python3.11 train_sac.py \
    --timesteps 5000000 \
    --checkpoint-freq 500000 \
    2>&1 | tee -a sac_training.log

echo ""
echo "=== Phase 1 complete at $(date) ==="
