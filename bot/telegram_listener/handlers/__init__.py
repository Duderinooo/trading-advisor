"""telegram_listener.handlers — per-domain command handler modules.

Phase 2 of the telegram_listener refactor: commands.py monolith → 6 domain
modules. Each handler still uses the same @telegram_handler decorator + shared
parsers from telegram_listener._common.
"""

from telegram_listener.handlers.analytics import audit_handler, stats_handler
from telegram_listener.handlers.callbacks import rec_callback_handler
from telegram_listener.handlers.confirm import confirm_handler
from telegram_listener.handlers.portfolio_info import dividend_handler, positions_handler
from telegram_listener.handlers.system import (
    help_handler, killstatus_handler, morning_handler, panic_handler, resume_handler,
)
from telegram_listener.handlers.trade_actions import (
    add_handler, cancel_handler, close_handler,
)
from telegram_listener.handlers.watch import (
    watch_handler, watchclear_handler, watchlist_handler, watchremove_handler,
)

__all__ = [
    "add_handler", "audit_handler", "cancel_handler", "close_handler",
    "confirm_handler", "dividend_handler", "help_handler", "killstatus_handler",
    "morning_handler", "panic_handler", "positions_handler",
    "rec_callback_handler", "resume_handler",
    "stats_handler", "watch_handler", "watchclear_handler", "watchlist_handler",
    "watchremove_handler",
]
