#!/bin/bash
# ── Export notebook to standalone HTML for sharing ─────────────────────────
# This runs the notebook top-to-bottom and saves a fully rendered HTML file.
# The HTML file is self-contained — charts, text, everything in one file.
# You can email it or upload it and anyone can open it in a browser.
#
# Usage:  bash ~/Documents/btc-wheel-bot/export_html.sh
# Output: ~/Documents/btc-wheel-bot/btc_wheel_bot_explainer.html

set -e

NOTEBOOK_DIR="$HOME/Documents/btc-wheel-bot"
NOTEBOOK="$NOTEBOOK_DIR/wheel_bot_explainer.ipynb"
OUTPUT_HTML="$NOTEBOOK_DIR/btc_wheel_bot_explainer.html"

echo "🔄  Executing notebook (this takes 30-60 seconds)..."

/usr/local/bin/jupyter nbconvert \
    --to html \
    --execute \
    --ExecutePreprocessor.timeout=300 \
    --output "$OUTPUT_HTML" \
    "$NOTEBOOK"

SIZE=$(du -sh "$OUTPUT_HTML" | cut -f1)
echo ""
echo "✅  Done! Exported to:"
echo "   $OUTPUT_HTML ($SIZE)"
echo ""
echo "📤  To share with your brother:"
echo "   1. Email the HTML file as an attachment"
echo "   2. Or upload to Google Drive / Dropbox and share the link"
echo "   3. Or drag it into any web browser — it works offline, no install needed"
