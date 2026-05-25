"""Red-team critic: bear-Claude reviews a finished bull-rec.

Single tool-forced Claude call. Cache key = stable bear-critic system prompt
(only user-message changes per rec). Returns critique dict or None on failure.
"""

import logging

from anthropic import Anthropic

import config

from core.llm.prompt.prompts import RED_TEAM_SYSTEM, RED_TEAM_TOOL
from core.llm.serialization import dump
from core.llm.telemetry.api_usage import increment_usage
from core.llm.telemetry.call_log import log_claude_call


logger = logging.getLogger(__name__)


# Lazily instantiated — only needed for red-team calls. Keeps module import light.
_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def run_red_team(
    rec: dict, snap: dict | None, regime: str, model: str,
) -> dict | None:
    """Bear-case critique of a proposed entry. Returns critique dict or None."""
    payload = {
        "ticker": rec.get("ticker"),
        "entry_price": rec.get("entry_price"),
        "stop_loss": rec.get("stop_loss"),
        "take_profit": rec.get("take_profit"),
        "size_eur": rec.get("size_eur"),
        "conviction": rec.get("conviction"),
        "p_win": rec.get("p_win"),
        "setup_type": rec.get("setup_type"),
        "top_fail_mode": rec.get("top_fail_mode"),
        "thesis": rec.get("thesis"),
        "confluence_score": rec.get("confluence_score"),
        "confluence_items": rec.get("confluence_items"),
        "correlations": rec.get("correlations"),
    }
    snap_slim = None
    if isinstance(snap, dict):
        keys = (
            "price", "prev_close", "change_pct", "rsi14", "macd", "macd_signal",
            "ma20", "ma50", "ma200", "atr14_pct", "volume_ratio", "spread_pct",
            "wk_trend", "rs_20d_vs_index_pct",
            "analyst_rec_key", "analyst_upside_pct", "analyst_count",
        )
        snap_slim = {k: snap.get(k) for k in keys if snap.get(k) is not None}

    user_msg = (
        "Kritisiere folgende Long-Empfehlung. Bear-Sicht. Tool-Call PFLICHT.\n\n"
        f"## Empfehlung\n{dump(payload)}\n\n"
        f"## Markt-Kontext für {payload['ticker']}\n{dump(snap_slim or {})}\n\n"
        f"## Regime\n{regime}"
    )

    # USE_AGENTS=True → CLI only. API fallback removed per user-decision
    # 2026-05-25: zero API spend, full subscription routing.
    use_agents = bool(getattr(config, "USE_AGENTS", False))
    if use_agents:
        try:
            from agents._lib.runner import call_claude_agent
            resp = call_claude_agent(
                mode="red_team",
                system_prompt=RED_TEAM_SYSTEM,
                user_message=user_msg,
                tools=[RED_TEAM_TOOL],
                max_tokens=400,
                model=model,
                force_any_tool=True,
            )
        except Exception as e:
            logger.warning("Red-team CLI failed (no fallback): %s — skipping critique", e)
            return None
    else:
        try:
            resp = _get_client().messages.create(
                model=model,
                max_tokens=400,
                system=[{
                    "type": "text",
                    "text": RED_TEAM_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=[RED_TEAM_TOOL],
                tool_choice={"type": "tool", "name": "submit_critique"},
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as e:
            logger.warning("Red-team call failed: %s", e)
            return None

    increment_usage(forced=False)

    rt_tool_calls = []
    rt_data = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "submit_critique":
            rt_data = block.input or {}
            rt_tool_calls.append({"name": "submit_critique", "input": rt_data})
            usage = getattr(resp, "usage", None)
            if usage is not None:
                logger.info(
                    "Red-team call: in=%s out=%s cache_read=%s cache_write=%s",
                    getattr(usage, "input_tokens", None),
                    getattr(usage, "output_tokens", None),
                    getattr(usage, "cache_read_input_tokens", None),
                    getattr(usage, "cache_creation_input_tokens", None),
                )

    log_claude_call(
        mode="red_team",
        model=model,
        turn=1,
        system_prompt=RED_TEAM_SYSTEM,
        user_message=user_msg,
        text_response="",
        tool_calls=rt_tool_calls,
        usage=getattr(resp, "usage", None),
        extra={"target_ticker": payload.get("ticker")},
    )

    if rt_data is not None:
        return rt_data
    logger.warning("Red-team returned no tool_use block — skipping critique")
    return None
