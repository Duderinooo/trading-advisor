"""Claude API call + tool_use response parsing + dispatch.

call_claude issues the request with mode-specific tools + budget.
extract_tool_use pulls text + tool_use blocks out of the response.
dispatch_tool_calls routes each rec to its handler in core.tool_handlers.
"""

import logging
from dataclasses import dataclass

from anthropic import Anthropic

from core.llm.prompt.context import RequestContext
from core.llm.handlers.recs.add import handle_add_recommendation
from core.llm.handlers.recs.entry import handle_entry_recommendation
from core.llm.handlers.recs.exit_rec import handle_exit_recommendation
from core.llm.handlers.recs.update import handle_update_targets


@dataclass
class Recs:
    """Typed container for the four rec kinds a single Claude call can emit.
    Each field is either the persisted-rec dict or None (gate-blocked / not called)."""
    entry: dict | None = None
    add: dict | None = None
    update: dict | None = None
    exit: dict | None = None


logger = logging.getLogger(__name__)

client = Anthropic()


# Maps tool_use.name → key in the extracted payload dict.
_TOOL_NAME_TO_KEY = {
    "set_watch_levels": "watch",
    "recommend_entry": "entry",
    "recommend_add_to_position": "add",
    "update_position_targets": "update",
    "recommend_exit": "exit",
    "submit_pass": "pass",
}


def call_claude(
    *, mode: str, system_prompt: str, user_message: str,
    tools: list | None, max_tokens: int, model: str, force_any_tool: bool,
):
    """Issue the Claude request. Returns the raw Anthropic response object."""
    create_kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        "messages": [{"role": "user", "content": user_message}],
    }
    if tools:
        create_kwargs["tools"] = tools
        if force_any_tool:
            # "any" = model MUST use one of the provided tools. Free text alongside
            # is allowed but discarded backend-side.
            create_kwargs["tool_choice"] = {"type": "any"}

    response = client.messages.create(**create_kwargs)

    usage = getattr(response, "usage", None)
    if usage is not None:
        logger.info(
            "Claude call: mode=%s turn=1 in=%s out=%s cache_read=%s cache_write=%s",
            mode,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
            getattr(usage, "cache_read_input_tokens", None),
            getattr(usage, "cache_creation_input_tokens", None),
        )
    return response


def extract_tool_use(response) -> dict:
    """Pull text + tool_use blocks out of the response.

    Returns dict with:
      text_parts:           list of text-block strings
      tool_use_blocks:      raw block objects (for log_claude_call)
      watch_tool_called:    bool — was set_watch_levels invoked?
      watch_tool_raw_input: full input dict for set_watch_levels (None if not called)
      new_levels:           the "levels" list from set_watch_levels (None if not called)
      payload:              {key: tool_input_dict} for entry/add/update/exit
      pass_reason:          submit_pass.reason or None
    """
    text_parts: list[str] = []
    tool_use_blocks: list = []
    watch_tool_called = False
    watch_tool_raw_input: dict | None = None
    new_levels = None
    pass_reason: str | None = None
    payload: dict = {"entry": None, "add": None, "update": None, "exit": None}

    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            name = getattr(block, "name", None)
            data = block.input or {}
            tool_use_blocks.append(block)
            key = _TOOL_NAME_TO_KEY.get(name)
            if key == "watch":
                watch_tool_called = True
                watch_tool_raw_input = data
                new_levels = data.get("levels", [])
            elif key == "pass":
                pass_reason = data.get("reason", "")
            elif key in payload:
                payload[key] = data

    return {
        "text_parts": text_parts,
        "tool_use_blocks": tool_use_blocks,
        "watch_tool_called": watch_tool_called,
        "watch_tool_raw_input": watch_tool_raw_input,
        "new_levels": new_levels,
        "payload": payload,
        "pass_reason": pass_reason,
    }


def format_analysis_text(text_parts: list[str], pass_reason: str | None) -> str:
    """Dedupe consecutive identical lines (Sonnet sometimes emits the same status
    line in multiple text-blocks when tool_use is sandwiched between)."""
    raw = "\n".join(t for t in text_parts if t).strip()
    dedup: list[str] = []
    for ln in raw.split("\n"):
        if dedup and ln.strip() == dedup[-1].strip():
            continue
        dedup.append(ln)
    if not dedup and pass_reason:
        return f"PASS: {pass_reason}"
    return "\n".join(dedup) or "(keine Text-Analyse)"


def dispatch_tool_calls(payload: dict, ctx: RequestContext, model: str) -> Recs:
    """Route each populated rec to its handler. Returns a Recs container."""
    recs = Recs()
    if payload["entry"]:
        recs.entry = handle_entry_recommendation(
            payload["entry"],
            mode=ctx.mode, market_data=ctx.market_data, market_ctx=ctx.market_ctx,
            regime=ctx.regime, cash=ctx.cash, model=model,
        )
    if payload["add"]:
        recs.add = handle_add_recommendation(payload["add"], ctx.market_data)
    if payload["update"]:
        recs.update = handle_update_targets(payload["update"])
    if payload["exit"]:
        recs.exit = handle_exit_recommendation(payload["exit"], ctx.market_data)
    return recs
