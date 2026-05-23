"""Telegram command listener — backwards-compat entry point.

Re-exports start_listener_thread so existing imports
    from telegram_listener import start_listener_thread
keep working unchanged after splitting the monolith into a package.
"""

from telegram_listener.runtime import start_listener_thread

__all__ = ["start_listener_thread"]
