"""Drain the dashboard's command queue.

The read-only web app enqueues Accept/Cancel/Fire actions into the web_commands
SQLite table; this runs each main-loop tick, executes them under portfolio_lock
(the bot owns every portfolio.json write), and marks each command done/error.
Runs regardless of market hours so accepts process promptly.
"""

import logging

from core import load_portfolio, save_portfolio, portfolio_lock
from core.proposals import apply_command
from core.portfolio import web_commands_store
from notifier import send_alert


logger = logging.getLogger("trading_advisor.web_commands")


def drain_web_commands() -> None:
    cmds = web_commands_store.pending_commands()
    if not cmds:
        return
    for cmd in cmds:
        ok = False
        msg = ""
        try:
            with portfolio_lock:
                portfolio = load_portfolio()
                ok, msg = apply_command(portfolio, cmd)
                if ok:
                    save_portfolio(portfolio)
        except Exception as e:
            logger.exception("web command %s failed", cmd.get("id"))
            ok, msg = False, f"exception: {e}"

        web_commands_store.mark_command(
            cmd["id"], "done" if ok else "error", msg,
        )
        logger.info(
            "web command #%s %s/%s → %s: %s",
            cmd.get("id"), cmd.get("action"), cmd.get("ticker"),
            "OK" if ok else "ERROR", msg,
        )
        if cmd.get("action") == "fire":
            send_alert(
                f"{'✅' if ok else '❌'} WEB FIRE: {cmd.get('ticker')}",
                msg + ("\nAuf TR ausführen." if ok else ""),
            )
