"""Persistent log of every Claude API call for the web /brain inspection page.

Writes one JSON object per line to claude_calls.jsonl. Captures mode, tokens,
cost-relevant cache stats, the user message, the text response, and any tool
calls — enough to reconstruct what the bot was thinking at any past tick.

System prompts are large + cache-stable, so we log only a hash and (one-time
per hash) write the full prompt to a sidecar file. The /brain page can fetch
the prompt body on demand.
"""

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CALL_LOG_PATH = os.path.join(_BASE, "claude_calls.jsonl")
_PROMPT_DIR = os.path.join(_BASE, ".system_prompts")

# Bound the per-call payload so a runaway prompt doesn't blow up the JSONL
# (and the dashboard's JSON.parse). 8KB ≈ 2000 tokens of user message — plenty.
_MAX_USER_MSG = 8000
_MAX_TEXT_RESP = 4000


def _ensure_prompt_dir() -> None:
    try:
        os.makedirs(_PROMPT_DIR, exist_ok=True)
    except OSError:
        pass


def _persist_system_prompt(system_prompt: str, h: str) -> None:
    """Write system prompt body once per hash. Idempotent."""
    if not system_prompt:
        return
    _ensure_prompt_dir()
    path = os.path.join(_PROMPT_DIR, f"{h}.txt")
    if os.path.exists(path):
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(system_prompt)
    except OSError:
        logger.exception("Failed to persist system prompt %s", h)


def _truncate(s: str, limit: int) -> str:
    if not s:
        return ""
    if len(s) <= limit:
        return s
    return s[:limit] + f"…[truncated {len(s) - limit} chars]"


def log_claude_call(
    *,
    mode: str,
    model: str,
    turn: int,
    system_prompt: str,
    user_message: str,
    text_response: str,
    tool_calls: list[dict[str, Any]] | None,
    usage: Any,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one entry to claude_calls.jsonl. Failures are swallowed (logging
    must never break the trading loop)."""
    try:
        sys_hash = hashlib.sha256((system_prompt or "").encode("utf-8")).hexdigest()[:12]
        _persist_system_prompt(system_prompt, sys_hash)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "model": model,
            "turn": turn,
            "tokens": {
                "input": getattr(usage, "input_tokens", None),
                "output": getattr(usage, "output_tokens", None),
                "cache_read": getattr(usage, "cache_read_input_tokens", None) or 0,
                "cache_write": getattr(usage, "cache_creation_input_tokens", None) or 0,
            },
            "system_hash": sys_hash,
            "user_message": _truncate(user_message, _MAX_USER_MSG),
            "text_response": _truncate(text_response or "", _MAX_TEXT_RESP),
            "tool_calls": tool_calls or [],
        }
        if extra:
            entry["extra"] = extra
        with open(_CALL_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Failed to log Claude call (mode=%s)", mode)


def read_system_prompt(sys_hash: str) -> str | None:
    """Used by web /api/claude-calls/prompt route. Returns None if hash unknown."""
    if not sys_hash or "/" in sys_hash or ".." in sys_hash:
        return None
    path = os.path.join(_PROMPT_DIR, f"{sys_hash}.txt")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None
