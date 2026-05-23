"""Claude-powered portfolio analysis orchestrator.

Pipeline (each step lives in its own module):
1. core.request_context.build_request_context — load portfolio, fetch market
   data, pre-filter, classify state.
2. core.prompt_builder.build_user_message — compose Claude user message.
3. core.response_parser.call_claude — issue request with mode-specific tools.
4. core.response_parser.extract_tool_use — pull text + tool_use blocks.
5. core.response_parser.dispatch_tool_calls — route recs to handlers.
6. core.persist.persist_results — lock-scoped write of recs + watch-levels + trace.

Engine-gate enforcement lives in core.tool_handlers.
"""

import logging

from dotenv import load_dotenv

import config
from memory import log_analysis, MEMPALACE_AVAILABLE

from core.llm.telemetry.api_usage import can_make_api_call, increment_usage
from core.llm.telemetry.call_log import log_claude_call
from core.metrics import compute_correlation_snapshot
from core.llm.handlers.persist import persist_results
from core.llm.prompt.builder import MODE_CONFIG, build_user_message
from core.llm.prompt.prompts import STRATEGY_SYSTEM
from core.llm.prompt.context import build_request_context
from core.llm.handlers.parser import (
    call_claude, dispatch_tool_calls, extract_tool_use, format_analysis_text,
)
from core.llm.handlers.tools import auto_paper_open
from core.llm.telemetry.trace import build_trace, log_trace_warnings, trace_key


load_dotenv()
logger = logging.getLogger(__name__)


def analyze_portfolio(
    mode: str = "standard",
    event_context: str = None,
    force: bool = False,
    bypass_cooldown: bool = False,
) -> str:
    """Run portfolio analysis with Claude.

    Modes:
    - "morning":  Full daily briefing + watch-level planning (Sonnet)
    - "opening":  Lightweight gap-check at market open (Haiku)
    - "event":    Quick analysis triggered by a watch-level hit / price-alert
    - "standard": Regular analysis

    force: Bypass regular cooldown for high-priority situations.
    bypass_cooldown: Manual override (/morning) — skip even forced-cooldown.
    """
    is_high_priority = mode in ("morning", "opening", "event") or force
    allowed, reason = can_make_api_call(
        force=is_high_priority, bypass_cooldown=bypass_cooldown,
    )
    if not allowed:
        return f"⚠️ Analysis skipped: {reason}"

    cfg = MODE_CONFIG.get(mode) or MODE_CONFIG["standard"]
    ctx = build_request_context(mode, event_context)

    system_prompt = STRATEGY_SYSTEM
    if cfg["base_prompt"]:
        system_prompt += "\n\n" + cfg["base_prompt"]
    system_prompt += cfg["suffix"]

    user_message = build_user_message(ctx)
    model = cfg["model"]

    response = call_claude(
        mode=mode,
        system_prompt=system_prompt,
        user_message=user_message,
        tools=cfg["tools"],
        max_tokens=cfg["max_tokens"],
        model=model,
        force_any_tool=cfg["force_any_tool"],
    )
    increment_usage(forced=is_high_priority)

    extracted = extract_tool_use(response)
    log_claude_call(
        mode=mode,
        model=model,
        turn=1,
        system_prompt=system_prompt,
        user_message=user_message,
        text_response="\n".join(t for t in extracted["text_parts"] if t),
        tool_calls=[
            {"name": tb.name, "input": tb.input or {}}
            for tb in extracted["tool_use_blocks"]
        ],
        usage=getattr(response, "usage", None),
        extra={"event_context": event_context} if event_context else None,
    )

    analysis_text = format_analysis_text(extracted["text_parts"], extracted["pass_reason"])
    recs = dispatch_tool_calls(extracted["payload"], ctx, model)

    if MEMPALACE_AVAILABLE:
        log_analysis(analysis_text, mode, event_context)

    corr_matrix = compute_correlation_snapshot(ctx.portfolio) if mode == "morning" else None

    trace: dict | None = None
    trace_k = trace_key(mode, event_context)
    if trace_k is not None:
        trace = build_trace(
            mode, response,
            watch_tool_called=extracted["watch_tool_called"],
            watch_tool_raw_input=extracted["watch_tool_raw_input"],
            new_levels=extracted["new_levels"],
            text_parts=extracted["text_parts"],
            max_tokens=cfg["max_tokens"],
        )
        log_trace_warnings(trace)

    persist_results(
        ctx,
        recs=recs,
        new_levels=extracted["new_levels"],
        corr_matrix=corr_matrix,
        trace=trace,
        trace_k=trace_k,
    )

    # Paper-portfolio mirror outside the real-portfolio lock (own paper_lock).
    if recs.entry is not None:
        try:
            auto_paper_open(recs.entry)
        except Exception:
            logger.exception("paper-portfolio auto-open failed")

    return analysis_text
