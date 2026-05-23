"""Runtime AppState — replaces module-level mutable globals.

Heartbeat ref + news-failure counters used to live as module-level lists/ints
in main.py. AppState bundles them so threading is explicit + state inspection is one object.
"""

from dataclasses import dataclass, field
import time


@dataclass
class AppState:
    """Process-lifetime mutable state. One singleton per run."""

    # Heartbeat: monotonic timestamp of last main-loop tick. Watchdog thread
    # reads from heartbeat[0] (1-element list for thread-safe pointer-swap).
    heartbeat: list = field(default_factory=lambda: [time.monotonic()])

    # News pipeline failure counter — alert after 3× consecutive failures.
    news_check_consec_failures: int = 0
    news_check_alert_sent: bool = False

    # Last heartbeat-write monotonic. Throttles portfolio.json writes to ≤1×/min.
    last_heartbeat_write: float = 0.0

    def tick(self) -> None:
        """Update heartbeat to current monotonic."""
        self.heartbeat[0] = time.monotonic()
