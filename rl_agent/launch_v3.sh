#!/bin/bash
# V3: MaskablePPO + action masking + LayerNorm + VecNormalize + low entropy
# Fixes overtrading root cause + proper discrete actions
# ~8 hours for 10M steps on Mac CPU
set -euo pipefail
cd "$(dirname "$0")"

echo "=== V3 Training — MaskablePPO ==="
echo "Started: $(date)"
echo "Fixes: action masking, low entropy (0.005), VecNormalize, 16 features"
echo "ETA: ~8 hours"
echo ""

caffeinate -dims python3.11 train_v3.py \
    --timesteps 10000000 \
    --checkpoint-freq 500000 \
    2>&1 | tee -a v3_training.log

echo ""
echo "=== V3 complete at $(date) ==="
