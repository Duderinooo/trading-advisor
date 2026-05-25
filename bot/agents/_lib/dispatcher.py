"""Agent dispatcher: glue that runs scheduled monitoring agents.

Called from main.py main-loop on each tick (cheap when nothing's due, blocks
for 10-60s when an agent fires). Independent of the trading LLM-calls in
core/llm/* — these are pure observability skills.

Each agent has its own feature flag in config.flags. Defaults all OFF so
rollout is opt-in per agent.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import config

from agents._lib.context import CONTEXT_BUILDERS
from agents._lib.runner import AgentRunError
from agents._lib.scheduler import due_agents, mark_ran
from agents._lib.skill_runner import run_skill

logger = logging.getLogger(__name__)

_REPO_DIR = Path(__file__).resolve().parents[3]
_INCIDENTS_DIR = _REPO_DIR / "docs" / "incidents"
_RESEARCH_DIR = _REPO_DIR / "research"
_TUNING_DIR = _REPO_DIR / "docs"
_BACKLOG_PATH = _REPO_DIR / "docs" / "backlog.md"
_CET = ZoneInfo("Europe/Berlin")


def _flag_enabled(agent_name: str) -> bool:
    """Per-agent flag: AGENT_<name> in config.flags (default False)."""
    flag_name = "agent_" + agent_name.replace("-", "_")
    flags = getattr(config, "FLAGS", {})
    flag = flags.get(flag_name)
    if flag is None:
        return False
    return bool(flag.enabled)


def _today_str() -> str:
    return datetime.now(_CET).strftime("%Y-%m-%d")


def _ts_compact() -> str:
    return datetime.now(_CET).strftime("%Y-%m-%d-%H%M")


def _persist_bug_watcher(output: str) -> str | None:
    """If output indicates real issues (not '✅ no new issues'), save to incidents
    + return summary line for Telegram."""
    output = output.strip()
    if not output:
        return None
    if "no new issues" in output.lower():
        return None
    _INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _INCIDENTS_DIR / f"{_ts_compact()}-bug-watcher.md"
    path.write_text(output, encoding="utf-8")
    # First line of first ISSUE = summary
    for line in output.splitlines():
        if line.startswith("## ISSUE:"):
            return f"🐛 bug-watcher: {line[10:].strip()} — {path.name}"
    return f"🐛 bug-watcher: {path.name}"


def _persist_health_inspector(output: str) -> str | None:
    output = output.strip()
    if not output:
        return None
    _INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _INCIDENTS_DIR / f"{_ts_compact()}-health-inspector.md"
    path.write_text(output, encoding="utf-8")
    verdict = "unknown"
    for line in output.splitlines():
        ll = line.lower()
        if "🔴" in line or "critical" in ll:
            verdict = "🔴 critical"
            break
        if "🟡" in line or "degraded" in ll:
            verdict = "🟡 degraded"
            break
        if "🟢" in line or "healthy" in ll:
            verdict = "🟢 healthy"
            break
    if verdict.startswith("🟢"):
        return None  # No alert when healthy
    return f"❤️‍🩹 health-inspector: {verdict} — {path.name}"


def _persist_eod_postmortem(output: str) -> str | None:
    """Parse `PATH: research/...md` blocks separated by `---FILE---` and write each."""
    output = output.strip()
    if not output or "NOTHING_TO_REVIEW" in output:
        return None
    _RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    skipped = []
    blocks = output.split("---FILE---")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        first_line, _, rest = block.partition("\n")
        if not first_line.startswith("PATH:"):
            continue
        target_rel = first_line[len("PATH:"):].strip()
        target_path = _REPO_DIR / target_rel
        if "(skip — file exists)" in rest or target_path.exists():
            skipped.append(target_path.name)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        # Strip the first code fence if present
        body = rest.strip()
        if body.startswith("```"):
            body = body.strip("`").lstrip("\n")
            if body.endswith("```"):
                body = body[:-3]
        target_path.write_text(body.strip() + "\n", encoding="utf-8")
        written.append(target_path.name)
    if not written:
        return None
    return f"📒 eod-postmortem: {len(written)} written, {len(skipped)} skipped"


def _persist_weekly_calibrator(output: str) -> str | None:
    output = output.strip()
    if not output:
        return None
    iso_week = datetime.now(_CET).strftime("%Y-W%V")
    path = _TUNING_DIR / f"tuning-suggestions-{iso_week}.md"
    path.write_text(output, encoding="utf-8")
    return f"🎚 weekly-calibrator: {path.name}"


def _persist_backlog_keeper(output: str) -> str | None:
    output = output.strip()
    if not output:
        return None
    _BACKLOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BACKLOG_PATH.write_text(output, encoding="utf-8")
    return f"📋 backlog-keeper: updated docs/backlog.md"


PERSISTERS = {
    "bug-watcher": _persist_bug_watcher,
    "health-inspector": _persist_health_inspector,
    "eod-postmortem": _persist_eod_postmortem,
    "weekly-calibrator": _persist_weekly_calibrator,
    "backlog-keeper": _persist_backlog_keeper,
}


def run_agent(name: str, *, force: bool = False) -> tuple[bool, str | None]:
    """Run one agent now.

    Returns (ok, telegram_alert_or_None). Persists output. Marks ran in
    scheduler regardless of success so we don't spam on persistent failure
    (will retry next interval).
    """
    if not force and not _flag_enabled(name):
        return False, f"agent {name} flag disabled"
    builder = CONTEXT_BUILDERS.get(name)
    persister = PERSISTERS.get(name)
    if builder is None or persister is None:
        return False, f"agent {name}: missing context-builder or persister"
    t0 = time.time()
    try:
        user_input = builder()
        output = run_skill(name, user_input)
    except AgentRunError as e:
        mark_ran(name, ok=False, meta={"error": str(e)[:200]})
        logger.exception("agent %s failed", name)
        return False, f"⚠️ {name} failed: {str(e)[:120]}"
    except Exception as e:
        mark_ran(name, ok=False, meta={"error": str(e)[:200]})
        logger.exception("agent %s crashed", name)
        return False, f"⚠️ {name} crashed: {str(e)[:120]}"
    duration_s = int(time.time() - t0)
    alert = persister(output)
    mark_ran(name, ok=True, meta={"duration_s": duration_s,
                                   "output_chars": len(output)})
    logger.info("agent %s ok in %ds (alert=%s)", name, duration_s, bool(alert))
    return True, alert


def tick(notify=None) -> list[str]:
    """Called from main-loop. Fires any due agents (gated by flags).

    notify: optional callable(msg) for Telegram-alerts (caller decides
    whether to wire it). Returns list of alert messages produced.
    """
    alerts: list[str] = []
    for name in due_agents():
        if not _flag_enabled(name):
            continue
        ok, alert = run_agent(name)
        if alert:
            alerts.append(alert)
            if notify is not None:
                try:
                    notify(alert)
                except Exception:
                    logger.exception("notify callback failed for %s", name)
    return alerts
