"""Backwards-compat re-export of all handlers from the new handlers/ subpackage.

Phase 2 of the telegram_listener split: commands.py is now a thin shim;
real handlers live in telegram_listener/handlers/*.py.
"""

from telegram_listener.handlers import (
    add_handler, audit_handler, cancel_handler, close_handler,
    confirm_handler, dividend_handler, help_handler, killstatus_handler,
    morning_handler, panic_handler, positions_handler, resume_handler,
    stats_handler, watch_handler, watchclear_handler, watchlist_handler,
    watchremove_handler,
)

__all__ = [
    "add_handler", "audit_handler", "cancel_handler", "close_handler",
    "confirm_handler", "dividend_handler", "help_handler", "killstatus_handler",
    "morning_handler", "panic_handler", "positions_handler", "resume_handler",
    "stats_handler", "watch_handler", "watchclear_handler", "watchlist_handler",
    "watchremove_handler",
]
