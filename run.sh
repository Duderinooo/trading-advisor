#!/bin/zsh
# Launch the trading-advisor bot from the bot/ subpackage.
# Resolves its own location so it works regardless of where the repo lives.
SCRIPT_DIR="${0:A:h}"
source "$SCRIPT_DIR/venv/bin/activate"
cd "$SCRIPT_DIR/bot"
python3 main.py &
BOT_PID=$!
caffeinate -is -w $BOT_PID
wait $BOT_PID
