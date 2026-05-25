"""Feature flags — single source of truth for boolean system toggles.

Why a registry vs plain constants:
- expiry-date metadata: temporary experiments self-flag for cleanup review
- rationale: every flag carries its "why" — prevents drift into permanent technical debt
- audit surface: expired_flags() lists toggles past their review date
- typed: is_enabled(name) instead of bare globals — typo-safe lookup

Backwards-compat: legacy module-level constants (RED_TEAM_ENABLED etc.) are
exposed as plain bools derived from FLAGS so existing call-sites keep working.
New code should use `is_enabled("red_team")` instead.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureFlag:
    """One feature toggle with metadata."""
    name: str
    enabled: bool
    expires_on: str | None  # ISO date "YYYY-MM-DD" or None for permanent
    rationale: str          # one-liner: why does this flag exist?


# Single source of truth. Edit values here; legacy constants below mirror.
FLAGS: dict[str, FeatureFlag] = {
    "red_team": FeatureFlag(
        name="red_team",
        enabled=True,
        expires_on=None,
        rationale="Bear-critic Claude call on entry recs — KILL/low-conf blocks + cooldown",
    ),
    "auto_split_tp": FeatureFlag(
        name="auto_split_tp",
        enabled=True,
        expires_on=None,
        rationale="Single-TP recs get 1R TP1 prepended for partial scale-out at BE",
    ),
    "risk_off_blocks_longs": FeatureFlag(
        name="risk_off_blocks_longs",
        enabled=True,
        expires_on=None,
        rationale="No new LONG when SPY < MA200 (conservative full-trust bias)",
    ),
    "news_require_open_or_watch": FeatureFlag(
        name="news_require_open_or_watch",
        enabled=True,
        expires_on=None,
        rationale="Skip Claude call for stock-news on tickers without position/watch",
    ),
    "use_agents": FeatureFlag(
        name="use_agents",
        enabled=True,
        expires_on=None,
        rationale="Route LLM calls through `claude` CLI (subscription-billed) instead of Anthropic API. Saves per-call cost; CLI ~3-10s slower per call.",
    ),
    # ---- Per-monitoring-agent flags (Phase B: observability skills). All
    # default OFF; flip individually after manual /telegram trigger verification.
    "agent_bug_watcher": FeatureFlag(
        name="agent_bug_watcher",
        enabled=True,
        expires_on=None,
        rationale="Hourly bug-watcher agent (log+pattern scan, writes docs/incidents/).",
    ),
    "agent_health_inspector": FeatureFlag(
        name="agent_health_inspector",
        enabled=True,
        expires_on=None,
        rationale="Twice-daily health-inspector agent (holistic state synth, writes docs/incidents/).",
    ),
    "agent_eod_postmortem": FeatureFlag(
        name="agent_eod_postmortem",
        enabled=True,
        expires_on=None,
        rationale="Daily 22:15 eod-postmortem agent (per-closed-trade research/ stubs).",
    ),
    "agent_weekly_calibrator": FeatureFlag(
        name="agent_weekly_calibrator",
        enabled=True,
        expires_on=None,
        rationale="Sunday weekly-calibrator agent (hit_stats + gate_FNR → docs/tuning-suggestions-*.md).",
    ),
    "agent_backlog_keeper": FeatureFlag(
        name="agent_backlog_keeper",
        enabled=True,
        expires_on=None,
        rationale="Sunday backlog-keeper agent (sync docs/backlog.md from incidents/ + research/ + git).",
    ),
}


def is_enabled(name: str) -> bool:
    """Lookup-by-name; KeyError on unknown flag (typo-safe)."""
    return FLAGS[name].enabled


def expired_flags(today: str | None = None) -> list[str]:
    """Names of flags whose expires_on has passed (for /flags-audit cmd).
    today: ISO date "YYYY-MM-DD". Defaults to today's date."""
    if today is None:
        from datetime import date
        today = str(date.today())
    return [
        f.name for f in FLAGS.values()
        if f.expires_on is not None and f.expires_on < today
    ]


# ---------------------------------------------------------------------------
# Backwards-compat: legacy module-level constants
# ---------------------------------------------------------------------------
# Existing call-sites read `config.RED_TEAM_ENABLED` etc. — keep working.
# New code should use is_enabled("red_team") for typo-safe lookup.

RED_TEAM_ENABLED = FLAGS["red_team"].enabled
AUTO_SPLIT_SINGLE_TP_AT_1R = FLAGS["auto_split_tp"].enabled
RISK_OFF_BLOCKS_LONGS = FLAGS["risk_off_blocks_longs"].enabled
NEWS_REQUIRE_OPEN_OR_WATCH = FLAGS["news_require_open_or_watch"].enabled
USE_AGENTS = FLAGS["use_agents"].enabled
