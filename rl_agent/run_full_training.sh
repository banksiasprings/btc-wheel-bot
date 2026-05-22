#!/usr/bin/env bash
# run_full_training.sh — Full RL training pipeline on real Deribit data.
#
# Steps:
#   1. Install Python dependencies
#   2. Fetch 3 years of real BTC price + IV from Deribit public API
#   3. Train PPO for 2,000,000 steps (real data)
#   4. Evaluate on holdout (last 30%) and print metrics
#
# Expected wall-clock time: 20–60 min on CPU (depends on hardware)
# Outputs:
#   rl_agent/data/btc_daily.csv        — real historical data
#   rl_agent/checkpoints/final_model.zip
#   rl_agent/checkpoints/model_*.zip   — periodic checkpoints
#
# Usage:
#   cd btc-wheel-bot/rl_agent && bash run_full_training.sh
#   bash run_full_training.sh --timesteps 500000   # faster test run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TIMESTEPS="${1:-2000000}"
DATA_CSV="data/btc_daily.csv"
MODEL_PATH="checkpoints/final_model.zip"

echo "========================================================"
echo "  BTC RL Agent — Full Training Pipeline"
echo "  Timesteps: $TIMESTEPS"
echo "========================================================"
echo ""

# 1. Dependencies
echo "[1/4] Installing Python dependencies ..."
pip3 install stable-baselines3 gymnasium pandas numpy scipy requests --break-system-packages --quiet
echo "      OK"
echo ""

# 2. Fetch real data
echo "[2/4] Fetching real BTC data from Deribit ..."
python3 fetch_deribit_data.py --days 1095
if [ ! -f "$DATA_CSV" ]; then
    echo "ERROR: Data fetch failed — CSV not found at $DATA_CSV"
    echo "       Falling back to synthetic GBM data for training."
    DATA_CSV=""
fi
echo ""

# 3. Training
echo "[3/4] Training PPO ($TIMESTEPS steps) ..."
if [ -n "$DATA_CSV" ]; then
    python3 train.py --timesteps "$TIMESTEPS" --checkpoint-freq 100000 --data "$DATA_CSV"
else
    python3 train.py --timesteps "$TIMESTEPS" --checkpoint-freq 100000
fi
echo ""

# 4. Evaluate
echo "[4/4] Evaluating on holdout data (last 30%) ..."
if [ ! -f "$MODEL_PATH" ]; then
    echo "ERROR: Model not found at $MODEL_PATH"
    exit 1
fi

set +e
if [ -n "$DATA_CSV" ]; then
    python3 evaluate.py --model "$MODEL_PATH" --data "$DATA_CSV"
else
    python3 evaluate.py --model "$MODEL_PATH"
fi
EVAL_EXIT=$?
set -e

echo ""
if [ $EVAL_EXIT -eq 0 ]; then
    echo "========================================================"
    echo "  RESULT: PASS — model meets quality thresholds"
    echo "  Next step: paper trade on Deribit testnet"
    echo "========================================================"
else
    echo "========================================================"
    echo "  RESULT: FAIL — thresholds not met on this run"
    echo "  Suggestions:"
    echo "    - Try more timesteps (5M+)"
    echo "    - Tune reward function in env.py"
    echo "    - Check data quality in data/btc_daily.csv"
    echo "========================================================"
fi

echo ""
echo "Model saved: $MODEL_PATH"
echo "Data used:   ${DATA_CSV:-synthetic GBM}"
echo "Done."
