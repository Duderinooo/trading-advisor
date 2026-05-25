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


def call_claude_agent(
    *,
    mode: str,
    system_prompt: str,
    user_message: str,
    tools: list[dict] | None,
    max_tokens: int,
    model: str,
    force_any_tool: bool,
    timeout_seconds: int = 180,
) -> _Response:
    """Drop-in replacement for handlers.parser.call_claude.

    Builds a `claude -p` invocation with structured-output schema covering the
    requested tool subset, parses the JSON, returns a mock Anthropic-shaped
    response.
    """
    if not tools:
        raise AgentRunError(f"agent mode requires tools list (mode={mode})")

    schema = _wrap_actions_schema(tools)
    claude_bin = _resolve_claude_bin()
    cli_model = _normalize_model(model)

    cmd = [
        claude_bin, "-p",
        "--model", cli_model,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--no-session-persistence",
        "--append-system-prompt", system_prompt,
        "--disable-slash-commands",
        user_message,
    ]

    t0 = time.time()
    error: str | None = None
    output_size = 0
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds,
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
        raise AgentRunError(f"claude CLI exit={proc.returncode}: {error}")

    raw = proc.stdout.strip()
    # CLI --output-format=json wraps the agent response in metadata. The
    # actual structured response is in .result (or .response — depends on
    # version). Probe both.
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        # Fall back: treat whole stdout as the structured response itself
        # (older CLI versions).
        envelope = {"result": raw}

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
