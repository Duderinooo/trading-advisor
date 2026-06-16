"""Subprocess wrapper around `claude -p`.

Routes LLM invocations through the Claude Code CLI so the marginal cost falls
on the user's Claude Code subscription instead of the per-call Anthropic API.

Drop-in replacement: `call_claude_agent()` returns a mock object that quacks
like an `anthropic.types.Message` (`.content[]` of TextBlock/ToolUseBlock-like
items, `.usage` with `input_tokens` / `output_tokens`). Downstream
`extract_tool_use()` works unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agents._lib.runs_db import log_run

logger = logging.getLogger(__name__)


class AgentRunError(RuntimeError):
    pass


class AgentRateLimitError(AgentRunError):
    """Subscription rate-limit hit. Caller should skip retry until reset."""
    pass


class AgentTransientError(AgentRunError):
    """Recoverable CLI failure (e.g. error_max_structured_output_retries — Haiku
    failed to converge on a valid tool-call after N retries). Intermittent:
    the next cycle usually succeeds. Caller should retry, not crash + alert.
    2026-06-05: US-open + price-check died on this while XETRA-open same morning
    ran clean — classic non-deterministic Haiku tool-call divergence."""
    pass


@dataclass
class _Block:
    """Mimics anthropic SDK content block (TextBlock / ToolUseBlock)."""
    type: str  # "text" | "tool_use"
    text: str | None = None
    name: str | None = None
    input: dict | None = None


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Response:
    content: list[_Block]
    usage: _Usage
    model: str
    stop_reason: str = "end_turn"


_MODEL_ALIASES = {
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5": "haiku",
    "claude-opus-4-8": "opus",
}


def _resolve_claude_bin() -> str:
    explicit = os.environ.get("CLAUDE_CLI_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    common = Path.home() / "Library/Application Support/Claude/claude-code/2.1.78/claude.app/Contents/MacOS/claude"
    if common.exists():
        return str(common)
    raise AgentRunError("`claude` CLI not found in PATH (set CLAUDE_CLI_BIN env to override)")


def _wrap_actions_schema(tool_subset: list[dict]) -> dict:
    """Build a JSON-schema where the agent emits {actions: [{tool, input}, ...]}.

    Each item is constrained via oneOf to match one of the supplied tool
    schemas. Mirrors API tool-use semantics in a single structured-output call.
    """
    return {
        "type": "object",
        "properties": {
            "narrative": {"type": "string", "maxLength": 2000},
            "actions": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {
                                "tool": {"const": t["name"]},
                                "input": t["input_schema"],
                            },
                            "required": ["tool", "input"],
                            "additionalProperties": False,
                        }
                        for t in tool_subset
                    ],
                },
            },
        },
        "required": ["actions"],
        "additionalProperties": False,
    }


def _normalize_model(model: str) -> str:
    """Map full API model name to CLI alias when possible. CLI accepts both."""
    return _MODEL_ALIASES.get(model, model)


_MODE_TIMEOUT_SECONDS = {
    "morning": 420,    # Sonnet + 5 tools + full portfolio context — needs headroom
    "opening": 240,
    "event": 180,
    "standard": 180,
    "red_team": 90,
}

# Per-call USD hard-cap (--max-budget-usd, only works with -p/--print). The CLI
# has no --max-tokens flag, so the output budget is a prompt soft-cap Haiku can
# ignore (2026-06-05: event call burned 4809 tok / 900 budget across an 11-turn
# tool-loop, $0.089). This caps runaway divergence loops at the cost layer:
# generous over the legit max (morning Sonnet ~$0.16, Haiku modes ~$0.09) so a
# normal call never trips it — only a stuck loop does. Trips → AgentTransientError
# path (retries next cycle).
_MODE_BUDGET_USD = {
    "morning": 0.40,   # Sonnet, big context — legit ~$0.16
    "opening": 0.30,   # 2026-06-10: Sonnet now (was Haiku) — higher per-call cost
    "event": 0.20,
    "standard": 0.20,
    "red_team": 0.15,
}


def call_claude_agent(
    *,
    mode: str,
    system_prompt: str,
    user_message: str,
    tools: list[dict] | None,
    max_tokens: int,
    model: str,
    force_any_tool: bool,
    timeout_seconds: int | None = None,
) -> _Response:
    """Drop-in replacement for handlers.parser.call_claude.

    Builds a `claude -p` invocation with structured-output schema covering the
    requested tool subset, parses the JSON, returns a mock Anthropic-shaped
    response.
    """
    # tools=None is valid: text-only mode (e.g. legacy `standard` analysis).
    # In that case we skip --json-schema and treat the entire `result` as a
    # single text block.
    schema = _wrap_actions_schema(tools) if tools else None
    claude_bin = _resolve_claude_bin()
    cli_model = _normalize_model(model)
    if timeout_seconds is None:
        timeout_seconds = _MODE_TIMEOUT_SECONDS.get(mode, 180)

    # CLI lacks a --max-tokens flag → soft-cap via prompt appendix.
    # 2026-06-16: the old polite wording was ignored — morning burned 10k tok /
    # 3500 budget, screened only 4 of 26 names, emitted 0 watch-levels. Firmer
    # framing + "finish the tool_calls before you run out" directive, since the
    # real failure was prose-thrash that never reached set_watch_levels. (The
    # candidate shortlist in context.py is the structural half of this fix.)
    if max_tokens and max_tokens > 0:
        system_prompt = (
            f"{system_prompt}\n\n"
            f"[OUTPUT BUDGET — HARD {max_tokens} tokens. Terse: at most one short "
            f"line of reasoning per candidate, no essays. Screen ALL candidates, "
            f"but SPEND THE BUDGET ON COMPLETING THE REQUIRED tool_calls — never "
            f"end with analysis and no tool_call. If the budget is tight, cut "
            f"prose first, tool_calls last.]"
        )

    cmd = [
        claude_bin, "-p",
        "--model", cli_model,
        "--output-format", "json",
        "--no-session-persistence",
        "--append-system-prompt", system_prompt,
        "--disable-slash-commands",
    ]
    budget_usd = _MODE_BUDGET_USD.get(mode)
    if budget_usd:
        cmd.extend(["--max-budget-usd", str(budget_usd)])
    if schema is not None:
        cmd.extend(["--json-schema", json.dumps(schema)])
    cmd.append(user_message)

    # CRITICAL: strip ANTHROPIC_API_KEY before spawning `claude` CLI. Otherwise
    # the CLI prefers API-key auth over OAuth/subscription, and every "via
    # subscription" call gets billed against the API key instead.
    # Incident 2026-05-25: €1+ API spend appeared in console despite
    # USE_AGENTS=True flag. Root cause: inherited env var → CLI used API path.
    subprocess_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    t0 = time.time()
    error: str | None = None
    output_size = 0
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds,
            env=subprocess_env,
        )
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.time() - t0) * 1000)
        log_run("call_claude_agent", mode=mode, model=cli_model,
                duration_ms=duration_ms, exit_code=-1, error="timeout")
        raise AgentRunError(f"claude CLI timed out after {timeout_seconds}s (mode={mode})") from e

    duration_ms = int((time.time() - t0) * 1000)
    output_size = len(proc.stdout or "")

    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or "")[:500]
        log_run("call_claude_agent", mode=mode, model=cli_model,
                duration_ms=duration_ms, exit_code=proc.returncode,
                output_size=output_size, error=error)
        # Recoverable CLI exits — retry next cycle, don't crash + alert:
        #  - structured-output retry exhaustion (non-deterministic tool-call
        #    divergence)
        #  - per-call budget cap hit (runaway loop guard, --max-budget-usd)
        if ("error_max_structured_output_retries" in error
                or "budget" in error.lower()):
            raise AgentTransientError(
                f"recoverable CLI exit (mode={mode}): {error[:120]}"
            )
        raise AgentRunError(f"claude CLI exit={proc.returncode}: {error}")

    raw = proc.stdout.strip()
    # Rate-limit detection: CLI returns exit=0 + is_error=true + result text
    # "You've hit your limit · resets X". Treat as recoverable skip, not crash.
    if '"is_error":true' in raw and "hit your limit" in raw:
        log_run("call_claude_agent", mode=mode, model=cli_model,
                duration_ms=duration_ms, exit_code=0, output_size=output_size,
                error="rate_limited")
        raise AgentRateLimitError(f"Subscription rate-limit hit (mode={mode})")
    # CLI --output-format=json wraps the agent response in metadata. The
    # actual structured response is in .result (or .response — depends on
    # version). Probe both.
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        # Fall back: treat whole stdout as the structured response itself
        # (older CLI versions).
        envelope = {"result": raw}

    # Tool-less (schema=None) text-only mode: result IS the full payload.
    # Wrap it as a single text block, no actions.
    if schema is None:
        text = (envelope.get("result") if isinstance(envelope, dict) else "") or ""
        text = text.strip()
        usage_data = envelope.get("usage") if isinstance(envelope, dict) else None
        if isinstance(usage_data, dict):
            usage = _Usage(
                input_tokens=int(usage_data.get("input_tokens") or 0),
                output_tokens=int(usage_data.get("output_tokens") or 0),
                cache_read_input_tokens=int(usage_data.get("cache_read_input_tokens") or 0),
                cache_creation_input_tokens=int(usage_data.get("cache_creation_input_tokens") or 0),
            )
        else:
            usage = _Usage()
        log_run("call_claude_agent", mode=mode, model=cli_model,
                duration_ms=duration_ms, exit_code=0, output_size=output_size,
                meta={"text_chars": len(text),
                      "input_tokens": usage.input_tokens,
                      "output_tokens": usage.output_tokens,
                      "tool_less": True})
        return _Response(
            content=[_Block(type="text", text=text)] if text else [],
            usage=usage, model=cli_model, stop_reason="end_turn",
        )

    # CLI envelope: `--json-schema` parsed result lives under `structured_output`.
    # `result` is the text-channel reply (usually empty when structured-output
    # is in use). Fall back through legacy key names for resilience.
    payload = (
        envelope.get("structured_output")
        if isinstance(envelope, dict) else None
    )
    if payload is None:
        payload_raw = (
            envelope.get("result") or envelope.get("response")
            or envelope.get("output") or envelope
        ) if isinstance(envelope, dict) else envelope
        if isinstance(payload_raw, str):
            payload_raw = payload_raw.strip()
            if payload_raw.startswith("```"):
                payload_raw = "\n".join(
                    l for l in payload_raw.splitlines() if not l.startswith("```")
                ).strip()
            try:
                payload = json.loads(payload_raw) if payload_raw else {}
            except json.JSONDecodeError as e:
                log_run("call_claude_agent", mode=mode, model=cli_model,
                        duration_ms=duration_ms, exit_code=proc.returncode,
                        output_size=output_size,
                        error=f"json parse: {e} :: {payload_raw[:200]}")
                raise AgentRunError(
                    f"claude CLI returned non-JSON payload: {payload_raw[:200]}",
                ) from e
        else:
            payload = payload_raw or {}

    narrative = (payload.get("narrative") or "").strip() if isinstance(payload, dict) else ""
    actions = payload.get("actions") or [] if isinstance(payload, dict) else []

    blocks: list[_Block] = []
    if narrative:
        blocks.append(_Block(type="text", text=narrative))
    for act in actions:
        if not isinstance(act, dict):
            continue
        tool_name = act.get("tool")
        tool_input = act.get("input") or {}
        if not tool_name:
            continue
        blocks.append(_Block(type="tool_use", name=tool_name, input=tool_input))

    # Usage: CLI envelope may have token counts under different keys depending
    # on version. Probe; default to 0 if unknown.
    usage_data = envelope.get("usage") if isinstance(envelope, dict) else None
    if isinstance(usage_data, dict):
        usage = _Usage(
            input_tokens=int(usage_data.get("input_tokens") or 0),
            output_tokens=int(usage_data.get("output_tokens") or 0),
            cache_read_input_tokens=int(usage_data.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(usage_data.get("cache_creation_input_tokens") or 0),
        )
    else:
        usage = _Usage()

    log_run("call_claude_agent", mode=mode, model=cli_model,
            duration_ms=duration_ms, exit_code=0, output_size=output_size,
            meta={"n_actions": len(actions),
                  "input_tokens": usage.input_tokens,
                  "output_tokens": usage.output_tokens})
    logger.info(
        "agent-CLI ok: mode=%s model=%s actions=%d duration=%dms",
        mode, cli_model, len(actions), duration_ms,
    )

    stop_reason = "tool_use" if actions else "end_turn"
    return _Response(content=blocks, usage=usage, model=cli_model,
                     stop_reason=stop_reason)
