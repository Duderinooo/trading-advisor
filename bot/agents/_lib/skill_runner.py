"""Skill-runner: invokes a markdown-defined agent via `claude -p` and
returns free-form markdown output.

Different from runner.py (tool-using LLM call for trading recs):
  - skill_runner produces ANALYSIS/REPORTS as markdown
  - no JSON-schema, no tool-use semantics
  - input = context string built from log/DB/file gathering
  - output = markdown returned to caller (saved to disk or sent to Telegram)

Skills are read from agents/skills/<name>.md and have YAML frontmatter:
  ---
  name: bug-watcher
  model: haiku | sonnet
  max_tokens: 4096
  timeout_seconds: 120
  ---
  <system prompt body>
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agents._lib.runner import _resolve_claude_bin, _normalize_model, AgentRunError
from agents._lib.runs_db import log_run

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


@dataclass
class SkillSpec:
    name: str
    model: str
    max_tokens: int
    timeout_seconds: int
    system_prompt: str


_SKILL_CACHE: dict[str, SkillSpec] = {}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split `---\\n<yaml>\\n---\\n<body>` into (meta_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw_meta = text[4:end].strip()
    body = text[end + 5:].lstrip()
    meta: dict = {}
    for line in raw_meta.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body


def load_skill(name: str) -> SkillSpec:
    """Load + cache a skill markdown definition."""
    if name in _SKILL_CACHE:
        return _SKILL_CACHE[name]
    path = _SKILLS_DIR / f"{name}.md"
    if not path.exists():
        raise AgentRunError(f"skill not found: {path}")
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    spec = SkillSpec(
        name=meta.get("name") or name,
        model=meta.get("model") or "haiku",
        max_tokens=int(meta.get("max_tokens") or 4096),
        timeout_seconds=int(meta.get("timeout_seconds") or 120),
        system_prompt=body.strip(),
    )
    _SKILL_CACHE[name] = spec
    return spec


def run_skill(name: str, user_input: str) -> str:
    """Run skill agent with user_input. Returns markdown response.

    Raises AgentRunError on timeout / CLI failure / empty response.
    """
    spec = load_skill(name)
    claude_bin = _resolve_claude_bin()
    cli_model = _normalize_model(spec.model)

    cmd = [
        claude_bin, "-p",
        "--model", cli_model,
        "--output-format", "json",
        "--no-session-persistence",
        "--append-system-prompt", spec.system_prompt,
        "--disable-slash-commands",
        user_input,
    ]

    # Strip ANTHROPIC_API_KEY so CLI uses OAuth/subscription, NOT API key.
    # See runner.py for incident 2026-05-25 root cause.
    subprocess_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=spec.timeout_seconds,
            env=subprocess_env,
        )
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.time() - t0) * 1000)
        log_run(f"skill:{name}", model=cli_model, duration_ms=duration_ms,
                exit_code=-1, error="timeout")
        raise AgentRunError(f"skill {name} timed out after {spec.timeout_seconds}s") from e

    duration_ms = int((time.time() - t0) * 1000)
    output_size = len(proc.stdout or "")

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:500]
        log_run(f"skill:{name}", model=cli_model, duration_ms=duration_ms,
                exit_code=proc.returncode, output_size=output_size, error=err)
        # 2026-07-07: quota + auth-blip now also surface as exit=1 (mirror of
        # the same classification in runner.call_claude_agent).
        if "hit your limit" in err:
            from agents._lib.runner import AgentRateLimitError, _notify_rate_limit_once
            _notify_rate_limit_once()
            raise AgentRateLimitError(f"Subscription rate-limit hit (skill={name})")
        if "authentication_error" in err:
            from agents._lib.runner import AgentTransientError
            raise AgentTransientError(f"CLI auth blip (skill={name}): {err[:120]}")
        raise AgentRunError(f"skill {name} CLI exit={proc.returncode}: {err}")

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        envelope = {"result": proc.stdout}

    # Rate-limit detection: skip instead of crash.
    if isinstance(envelope, dict) and envelope.get("is_error") and \
       "hit your limit" in (envelope.get("result") or ""):
        log_run(f"skill:{name}", model=cli_model, duration_ms=duration_ms,
                exit_code=0, output_size=output_size, error="rate_limited")
        from agents._lib.runner import AgentRateLimitError
        raise AgentRateLimitError(f"Subscription rate-limit hit (skill={name})")

    result_md = (envelope.get("result") if isinstance(envelope, dict) else None) or ""
    result_md = result_md.strip()

    usage = envelope.get("usage") if isinstance(envelope, dict) else None
    meta = {"result_chars": len(result_md)}
    if isinstance(usage, dict):
        meta["input_tokens"] = int(usage.get("input_tokens") or 0)
        meta["output_tokens"] = int(usage.get("output_tokens") or 0)

    log_run(f"skill:{name}", model=cli_model, duration_ms=duration_ms,
            exit_code=0, output_size=output_size, meta=meta)
    logger.info(
        "skill ok: %s model=%s duration=%dms result_chars=%d",
        name, cli_model, duration_ms, len(result_md),
    )

    return result_md
