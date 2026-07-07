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


_TELEGRAM_LIMIT = 3800  # leave headroom under 4096 hard limit


def _truncate_for_telegram(body: str, limit: int = _TELEGRAM_LIMIT) -> str:
    if len(body) <= limit:
        return body
    return body[:limit - 80].rstrip() + f"\n\n… (+{len(body) - limit} chars cut, full report on Dashboard)"


def _persist_bug_watcher(output: str) -> str | None:
    """Save incident file. NO Telegram alert — bug-worker picks these up
    autonomously (2026-05-26 change: user wants bugs auto-fixed, not paged)."""
    output = output.strip()
    if not output:
        return None
    if "no new issues" in output.lower():
        return None
    _INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _INCIDENTS_DIR / f"{_ts_compact()}-bug-watcher.md"
    path.write_text(output, encoding="utf-8")
    return None  # No Telegram — handed off to bug-worker


def _persist_health_inspector(output: str) -> str | None:
    """Save report file. NO Telegram alert — same pattern as bug-watcher."""
    output = output.strip()
    if not output:
        return None
    _INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _INCIDENTS_DIR / f"{_ts_compact()}-health-inspector.md"
    path.write_text(output, encoding="utf-8")
    return None


def _persist_eod_postmortem(output: str) -> str | None:
    """Parse `PATH: research/...md` blocks separated by `---FILE---` and write each."""
    output = output.strip()
    if not output or "NOTHING_TO_REVIEW" in output:
        return None
    _RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, str]] = []  # (filename, body)
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
        body = rest.strip()
        if body.startswith("```"):
            body = body.strip("`").lstrip("\n")
            if body.endswith("```"):
                body = body[:-3]
        body = body.strip()
        target_path.write_text(body + "\n", encoding="utf-8")
        written.append((target_path.name, body))
    if not written:
        return None
    # Inline summary: per-trade title + first ~400 chars each.
    parts = [f"📒 *eod-postmortem* — {len(written)} written, {len(skipped)} skipped\n"]
    for fname, body in written:
        # Take first ~400 chars per postmortem so user sees lessons inline.
        snippet = body[:400].rstrip()
        if len(body) > 400:
            snippet += "…"
        parts.append(f"\n▸ `{fname}`\n{snippet}")
    return _truncate_for_telegram("\n".join(parts))


def _persist_weekly_calibrator(output: str) -> str | None:
    output = output.strip()
    if not output:
        return None
    iso_week = datetime.now(_CET).strftime("%Y-W%V")
    path = _TUNING_DIR / f"tuning-suggestions-{iso_week}.md"
    path.write_text(output, encoding="utf-8")
    return _truncate_for_telegram(f"🎚 *weekly-calibrator* ({iso_week})\n\n{output}")


def _persist_backlog_keeper(output: str) -> str | None:
    output = output.strip()
    if not output:
        return None
    _BACKLOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BACKLOG_PATH.write_text(output, encoding="utf-8")
    return _truncate_for_telegram(f"📋 *backlog-keeper*\n\n{output}")


PERSISTERS = {
    "bug-watcher": _persist_bug_watcher,
    "health-inspector": _persist_health_inspector,
    "eod-postmortem": _persist_eod_postmortem,
    "weekly-calibrator": _persist_weekly_calibrator,
    "backlog-keeper": _persist_backlog_keeper,
}


def _log_signature() -> int:
    """Cheap fingerprint of bot.log + bot.err state — combines size + last-mtime.
    Used to skip log-scanning agents when nothing changed since their last run."""
    sig = 0
    for p in (Path(__file__).resolve().parents[2] / "bot.log",
              Path(__file__).resolve().parents[2] / "bot.err"):
        if p.exists():
            try:
                st = p.stat()
                sig = sig * 31 + int(st.st_size) + int(st.st_mtime)
            except Exception:
                pass
    return sig


def _should_skip_log_scan(name: str) -> bool:
    """True if log+err state is byte-identical to what this agent saw last run.
    Saves ~20-60s of LLM compute per skip. Cheap pre-fire optimization."""
    from agents._lib.scheduler import last_run_ts, _kv_get
    rec = _kv_get(name)
    if not rec or not isinstance(rec, dict):
        return False
    last_sig = rec.get("meta", {}).get("log_sig") if isinstance(rec.get("meta"), dict) else None
    if last_sig is None:
        return False
    return last_sig == _log_signature()


def _unprocessed_incident_count() -> int:
    """Count of bug-watcher incidents still needing worker attention."""
    if not _INCIDENTS_DIR.exists():
        return 0
    n = 0
    for p in _INCIDENTS_DIR.glob("*-bug-watcher.md"):
        try:
            if "<!-- bug-worker-status" not in p.read_text(encoding="utf-8", errors="replace"):
                n += 1
        except Exception:
            pass
    return n


def _run_bug_worker() -> str | None:
    """Bug-worker has its own orchestration (branch+commit lifecycle) — bypasses
    the standard skill-runner pattern. Returns Telegram alert text or None."""
    from agents._lib.bug_worker import run_once
    res = run_once()
    if res.status == "noop":
        return None
    if res.status == "fixed":
        push_state = "pushed" if res.pushed else "local-only"
        return (
            f"🔧 *bug-worker* fix on `{res.branch}` ({push_state})\n"
            f"commit `{res.commit_sha}`\n\n{res.summary}"
        )
    if res.status == "deferred":
        return f"⏭ *bug-worker* skipped `{res.incident_name}` — {res.summary}"
    if res.status == "failed":
        return (
            f"❌ *bug-worker* failed `{res.incident_name}`\n{res.summary[:1200]}"
        )
    # error
    return f"⚠️ *bug-worker* error `{res.incident_name or '?'}` — {res.summary[:300]}"


def run_agent(name: str, *, force: bool = False) -> tuple[bool, str | None]:
    """Run one agent now.

    Returns (ok, telegram_alert_or_None). Persists output. Marks ran in
    scheduler regardless of success so we don't spam on persistent failure
    (will retry next interval).
    """
    if not force and not _flag_enabled(name):
        return False, f"agent {name} flag disabled"

    # Cheap pre-checks (2026-05-26 audit): skip the LLM call entirely
    # when nothing's changed since last run. Saves 20-60s per skip.
    if not force:
        if name in ("bug-watcher", "health-inspector") and _should_skip_log_scan(name):
            logger.info("agent %s skipped — log state unchanged since last run", name)
            mark_ran(name, ok=True, meta={"skipped": "log unchanged",
                                          "log_sig": _log_signature()})
            return True, None
        if name == "bug-worker" and _unprocessed_incident_count() == 0:
            logger.info("bug-worker skipped — no unprocessed incidents")
            mark_ran(name, ok=True, meta={"skipped": "no incidents"})
            return True, None

    # bug-worker has its own subprocess + git lifecycle; bypass standard
    # skill-runner path.
    if name == "bug-worker":
        try:
            alert = _run_bug_worker()
        except Exception as e:
            mark_ran(name, ok=False, meta={"error": str(e)[:200]})
            logger.exception("bug-worker crashed")
            return False, f"⚠️ bug-worker crashed: {str(e)[:200]}"
        mark_ran(name, ok=True)
        return True, alert

    builder = CONTEXT_BUILDERS.get(name)
    persister = PERSISTERS.get(name)
    if builder is None or persister is None:
        return False, f"agent {name}: missing context-builder or persister"
    t0 = time.time()
    try:
        user_input = builder()
        output = run_skill(name, user_input)
    except Exception as e:
        from agents._lib.runner import AgentRateLimitError
        if isinstance(e, AgentRateLimitError):
            # Push next-run timestamp forward so we don't hammer during reset.
            # Use ok=True so the skip is silent (no failure-spam Telegram).
            logger.warning("agent %s rate-limited — skipping retry until next cycle", name)
            mark_ran(name, ok=True, meta={"skipped": "rate-limited"})
            return True, None  # No Telegram alert
        from agents._lib.runner import AgentTransientError
        if isinstance(e, AgentTransientError):
            # 2026-07-07: auth-blip / recoverable CLI exit — same silent-skip as
            # rate-limit (was falling into the AgentRunError branch → failure
            # Telegram for a self-healing condition).
            logger.warning("agent %s transient error — retry next cycle: %s", name, e)
            mark_ran(name, ok=True, meta={"skipped": "transient"})
            return True, None
        if isinstance(e, AgentRunError):
            mark_ran(name, ok=False, meta={"error": str(e)[:200]})
            logger.exception("agent %s failed", name)
            return False, f"⚠️ {name} failed: {str(e)[:120]}"
        mark_ran(name, ok=False, meta={"error": str(e)[:200]})
        logger.exception("agent %s crashed", name)
        return False, f"⚠️ {name} crashed: {str(e)[:120]}"
    duration_s = int(time.time() - t0)
    alert = persister(output)
    meta = {"duration_s": duration_s, "output_chars": len(output)}
    # Persist log signature so the pre-fire skip works next cycle.
    if name in ("bug-watcher", "health-inspector"):
        meta["log_sig"] = _log_signature()
    mark_ran(name, ok=True, meta=meta)
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
