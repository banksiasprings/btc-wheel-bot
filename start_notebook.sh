#!/bin/bash
# ── BTC Wheel Bot — Jupyter Launcher ──────────────────────────────────────
# Run this script to open the explainer notebook in your browser.
# Usage: bash ~/Documents/btc-wheel-bot/start_notebook.sh
#
# Options:
#   --voila     Launch as a clean read-only web app (no code visible) — great for sharing
#   --lab       Launch full JupyterLab (default)

set -e

NOTEBOOK_DIR="$HOME/Documents/btc-wheel-bot"
NOTEBOOK_FILE="wheel_bot_explainer.ipynb"
PORT=8888

cd "$NOTEBOOK_DIR"

MODE="${1:-}"

if [ "$MODE" = "--voila" ]; then
    echo "🚀  Starting Voilà web app on http://localhost:$PORT"
    echo "     Share this with your browser (or tunnel with ngrok for external access)"
    /usr/local/bin/voila "$NOTEBOOK_FILE" \
        --port=$PORT \
        --no-browser=False \
        --VoilaConfiguration.file_allowlist="['.*']"
else
    echo "📓  Starting JupyterLab on http://localhost:$PORT"
    echo "     Open the notebook: wheel_bot_explainer.ipynb"
    /usr/local/bin/jupyter-lab \
        --notebook-dir="$NOTEBOOK_DIR" \
        --port=$PORT \
        --no-browser \
        --ip=127.0.0.1 \
        --NotebookApp.token='' \
        --NotebookApp.password='' \
        --ServerApp.open_browser=True
fi
