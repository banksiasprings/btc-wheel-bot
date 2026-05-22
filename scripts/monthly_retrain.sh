#!/usr/bin/env bash
# monthly_retrain.sh — Re-fetches latest 3yr BTC data from Deribit and retrains PPO.
# Designed to run as a cron job on the 1st of each month.
# Outputs:
#   rl_agent/data/btc_daily.csv  (refreshed)
#   rl_agent/checkpoints/final_model.zip (new model — overwrites)
#   rl_agent/checkpoints/best_model.zip  (updated if new model beats old)
#   rl_agent/logs/retrain_YYYY-MM.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RL_DIR="$PROJECT_DIR/rl_agent"
LOG_DIR="$RL_DIR/logs"
MONTH=$(date +"%Y-%m")
LOG_FILE="$LOG_DIR/retrain_${MONTH}.log"

mkdir -p "$LOG_DIR"

echo "[$(date)] Monthly retrain starting" | tee -a "$LOG_FILE"

# 1. Fetch latest data
cd "$RL_DIR"
python3 fetch_deribit_data.py --days 1095 2>&1 | tee -a "$LOG_FILE"

# 2. Train 2M steps
python3 train.py --timesteps 2000000 --checkpoint-freq 100000 --data data/btc_daily.csv 2>&1 | tee -a "$LOG_FILE"

# 3. Evaluate — compare against existing best_model
python3 evaluate.py --model checkpoints/final_model.zip --data data/btc_daily.csv 2>&1 | tee -a "$LOG_FILE"
NEW_EXIT=$?

if [ $NEW_EXIT -eq 0 ]; then
    cp checkpoints/final_model.zip checkpoints/best_model.zip
    MSG="Monthly retrain PASSED — best_model.zip updated"
else
    MSG="Monthly retrain FAILED quality threshold — best_model.zip unchanged"
fi

echo "[$(date)] $MSG" | tee -a "$LOG_FILE"

# 4. Push ntfy.sh notification
curl -s -X POST https://ntfy.sh/bsf-voice-tasks \
    -H "Title: BTC Bot Monthly Retrain" \
    -d "$MSG — see $LOG_FILE" || true

# 5. Commit updated data + model
cd "$PROJECT_DIR"
git add rl_agent/data/btc_daily.csv rl_agent/checkpoints/best_model.zip rl_agent/logs/retrain_${MONTH}.log || true
git commit -m "auto: monthly retrain ${MONTH}" || echo "Nothing new to commit"
git push origin main || echo "Push failed — continuing"

echo "[$(date)] Done" | tee -a "$LOG_FILE"
