#!/bin/bash
cd ~/Documents/btc-wheel-bot
/usr/local/bin/python3.11 main.py --mode=testnet >> logs/btc-wheel-bot.log 2>&1 &
echo $!
