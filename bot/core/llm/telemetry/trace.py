"""Per-mode pipeline trace for /brain dashboard + Telegram visibility.

Mirrors what we wish we'd had on 2026-04-29 when set_watch_levels({}) silently
masked a max_tokens truncation as 'no setups today' for 3 days.
"""

import logging
from datetime import datetime


logger = logging.getLogger(__name__)


_TRACE_KEY_BY_MODE = {
    "morning": "last_morning_trace",
    "event": "last_event_trace",
    "geo": "last_event_trace",  # geo-news (Sonnet) shares the event trace bucket
    # opening uses event_context to pick xetra/us slot; resolved at call site.
}


def trace_key(mode: str, event_context: str | None) -> str | None:
    """Portfolio.json key for the trace. Opening splits xetra/us via context."""
    if mode in _TRACE_KEY_BY_MODE:
        return _TRACE_KEY_BY_MODE[mode]
    if mode == "opening":
        ctx = (event_context or "").upper()
        if "XETRA" in ctx:
            return "last_opening_trace_xetra"
        if " US " in f" {ctx} " or ctx.startswith("US"):
            return "last_opening_trace_us"
        return "last_opening_trace"
    return None


def build_trace(
    mode: str,
    response,
    *,
    watch_tool_called: bool,
    watch_tool_raw_input: dict | None,
    new_levels: list | None,
    text_parts: list[str],
    max_tokens: int,
) -> dict:
    """Capture pipeline state for /brain dashboard + Telegram /morning reply."""
    _stop = getattr(response, "stop_reason", None)
    _usage = getattr(response, "usage", None)
    _out_tok = getattr(_usage, "output_tokens", None) if _usage else None
    malformed = (
        watch_tool_called
        and watch_tool_raw_input is not None
        and "levels" not in watch_tool_raw_input
    )
    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "tool_called": watch_tool_called,
        "tool_input_keys": sorted(watch_tool_raw_input.keys()) if watch_tool_raw_input else [],
        "raw_levels_count": len(new_levels) if new_levels is not None else 0,
        "raw_tickers": [(lvl.get("ticker") or "?") for lvl in (new_levels or [])],
        "stop_reason": _stop,
        "output_tokens": _out_tok,
        "max_tokens_budget": max_tokens,
        "truncated": _stop == "max_tokens",
        "malformed_tool_input": malformed,
        "sonnet_text": ("\n".join(t for t in text_parts if t))[:400],
    }


def log_trace_warnings(trace: dict) -> None:
    """INFO summary + ERROR for failure-modes that masked the 2026-04-29 outage
    (no tool call / malformed input / truncated)."""
    mode = trace.get("mode") or "?"
    logger.info(
        "TRACE [%s]: tool_called=%s raw_levels=%d stop=%s out_tok=%s/%s truncated=%s malformed=%s text_preview=%r",
        mode,
        trace["tool_called"],
        trace["raw_levels_count"],
        trace["stop_reason"],
        trace["output_tokens"],
        trace["max_tokens_budget"],
        trace["truncated"],
        trace["malformed_tool_input"],
        trace["sonnet_text"][:120],
    )
    # Morning is the only mode that *requires* set_watch_levels — others may legitimately
    # call no watch-tool (recommend_entry / recommend_exit / no action).
    if mode == "morning" and not trace["tool_called"]:
        logger.error("TRACE [%s]: set_watch_levels NOT called", mode)
    elif trace["malformed_tool_input"]:
        logger.error(
            "TRACE [%s]: tool input malformed (keys=%s) — likely truncated",
            mode, trace["tool_input_keys"],
        )
    elif trace["truncated"]:
        logger.error(
            "TRACE [%s]: response truncated at max_tokens=%d — bump budget or shorten prompt",
            mode, trace["max_tokens_budget"],
        )
