"""Event detection + trade-state transitions.

Public surface preserved from old monolithic core/events.py:
- check_news_events       (news.py)
- detect_events           (watchlevels.py)
- should_analyze_events   (watchlevels.py)
- check_stop_loss_take_profit (sltp.py)
- check_price_alerts      (price_alerts.py)

Plus EventType string constants (types.py) for callers that want a typed
surface — bare strings still work for JSON-IO + existing comparisons.
"""

from core.events.news import check_news_events
from core.events.price_alerts import check_price_alerts
from core.events.sltp import check_stop_loss_take_profit
from core.events.types import EventType
from core.events.watchlevels import detect_events, should_analyze_events

__all__ = [
    "check_news_events",
    "check_price_alerts",
    "check_stop_loss_take_profit",
    "detect_events",
    "should_analyze_events",
    "EventType",
]
