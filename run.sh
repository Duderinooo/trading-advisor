#!/bin/zsh
source "/Users/malteollmann/trading-advisor/venv/bin/activate"
python3 "/Users/malteollmann/trading-advisor/main.py" &
BOT_PID=$!
caffeinate -is -w $BOT_PID
wait $BOT_PID
