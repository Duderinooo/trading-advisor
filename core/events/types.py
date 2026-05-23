"""Centralized event-type string constants.

Use EventType.X instead of bare "X" strings to catch typos at lookup time.
Values are plain strings so existing dict-comparisons (`e["type"] == "NEWS_STOCK"`)
and JSON IO keep working without migration; this just adds a typed surface
for callers that want it.
"""


class EventType:
    # News detection
    NEWS_GEO = "NEWS_GEO"
    NEWS_STOCK = "NEWS_STOCK"

    # Watch-level detection
    WATCH_LEVEL_HIT = "WATCH_LEVEL_HIT"
    WATCH_LEVEL_NOTIFY = "WATCH_LEVEL_NOTIFY"
    WATCH_INVALIDATED = "WATCH_INVALIDATED"

    # SL/TP loop
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    TAKE_PROFIT_HIT = "TAKE_PROFIT_HIT"
    PARTIAL_TP_HIT = "PARTIAL_TP_HIT"
    BREAK_EVEN_SHIFT = "BREAK_EVEN_SHIFT"
    TRAILING_ACTIVATED = "TRAILING_ACTIVATED"
    TRAILING_STOP_MOVED = "TRAILING_STOP_MOVED"
    STOP_LOSS_WARNING = "STOP_LOSS_WARNING"

    # Price alerts
    DROP = "DROP"
    RISE = "RISE"
