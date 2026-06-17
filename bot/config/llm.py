"""LLM call routing + rate limits + model selection."""

# Forced-Call Rate-Limit (Geo-News Spam-Schutz)
MIN_MINUTES_BETWEEN_FORCED_ANALYSES = 15

# API usage - ROI driven, not hard capped
MAX_ANALYSES_PER_DAY = 20          # Safety cap, but shouldn't hit it normally
MIN_MINUTES_BETWEEN_ANALYSES = 45  # Swing braucht keine Hektik
ANALYSIS_COST_EUR = 0.01           # ~cost per Haiku call (gemessen 2026-04, event/opening/news)

# Morning candidate shortlist: max non-position tickers fed to the morning LLM.
# 2026-06-16: after the watchlist grew 14→26, Sonnet thrashed on the full set
# (258s / 10k-tok run, screened only 4 names, emitted 0 watch-levels). Engine
# pre-ranks by precomputed state (tier + entry_state − red_flags) and hands
# Sonnet only the top-N; open positions + existing watch-levels always stay on
# top of this. Keeps the screen completable within the soft token budget.
MORNING_CANDIDATE_SHORTLIST = 12

# Models: Sonnet für Morning-Brief, Haiku für Event-Checks (günstig, schnell).
# 2026-06-16: Opus-Versuch zurückgerollt — Opus beendet im CLI-Structured-Output-
# Harness mit stop=end_turn + 0 Tool-Calls (forced-tool greift nicht), liefert
# also GAR keine Recs (schlechter als Sonnet). Stattdessen: Sonnet + verschärftes
# Prompt-Mandat (emit EVERY qualifying setup als Tool-Call) gegen das Drop-Problem.
CLAUDE_MODEL_MORNING = "claude-sonnet-4-6"
CLAUDE_MODEL_EVENT = "claude-haiku-4-5"
CLAUDE_MODEL = CLAUDE_MODEL_EVENT  # Default/Fallback für Standard-Mode
