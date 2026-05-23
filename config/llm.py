"""LLM call routing + rate limits + model selection."""

# Forced-Call Rate-Limit (Geo-News Spam-Schutz)
MIN_MINUTES_BETWEEN_FORCED_ANALYSES = 15

# API usage - ROI driven, not hard capped
MAX_ANALYSES_PER_DAY = 20          # Safety cap, but shouldn't hit it normally
MIN_MINUTES_BETWEEN_ANALYSES = 45  # Swing braucht keine Hektik
ANALYSIS_COST_EUR = 0.01           # ~cost per Haiku call (gemessen 2026-04, event/opening/news)

# Models: Sonnet für Morning-Brief (Senior-Reasoning, 1x/Tag), Haiku für Event-Checks (günstig, schnell)
CLAUDE_MODEL_MORNING = "claude-sonnet-4-6"
CLAUDE_MODEL_EVENT = "claude-haiku-4-5"
CLAUDE_MODEL = CLAUDE_MODEL_EVENT  # Default/Fallback für Standard-Mode
