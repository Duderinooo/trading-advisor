"""Test suite — pure-logic unit tests for the trading bot.

Run from the repo root:
    ./venv/bin/python -m unittest discover -s tests -v

Scope: deterministic, no network, no portfolio.json mutation. Only pure
functions in core.portfolio / core.events / core.livefeed / macro are covered.
I/O paths (load/save_portfolio, Claude calls, Telegram, market_data fetch) are
out of scope by design — see CLAUDE.md "Test surface".
"""

