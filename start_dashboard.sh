#!/bin/bash
cd ~/Documents/btc-wheel-bot
/usr/local/bin/python3.11 -m streamlit run dashboard_ui.py --server.port 8501 --server.headless true >> /tmp/streamlit.log 2>&1 &
echo $!
