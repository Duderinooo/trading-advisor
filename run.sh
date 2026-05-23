#!/bin/zsh
# Launch the trading-advisor bot from the bot/ subpackage.
# 2026-05-23: paths updated after bot/ + web/ separation.
source "/Users/malteollmann/trading-advisor/venv/bin/activate"
cd "/Users/malteollmann/trading-advisor/bot"
python3 main.py &
BOT_PID=$!
caffeinate -is -w $BOT_PID
wait $BOT_PID
