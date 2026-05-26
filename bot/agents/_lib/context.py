"""Per-agent context gatherers.

Each skill agent needs different input sources (log tails, DB queries, file
listings). These functions assemble that input into a single string ready for
the agent's `user_message`. Read-only — no mutations.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.db import connect

logger = logging.getLogger(__name__)

_BOT_DIR = Path(__file__).resolve().parents[2]
_REPO_DIR = _BOT_DIR.parent
_LOG_PATH = _BOT_DIR / "bot.log"
_ERR_PATH = _BOT_DIR / "bot.err"
_INCIDENTS_DIR = _REPO_DIR / "docs" / "incidents"
_RESEARCH_DIR = _REPO_DIR / "research"
_TUNING_DIR = _REPO_DIR / "docs"
_BACKLOG_PATH = _REPO_DIR / "docs" / "backlog.md"
_CET = ZoneInfo("Europe/Berlin")


def _tail(path: Path, n: int = 200) -> str:
    if not path.exists():
        return f"(file missing: {path})"
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, n * 250)
            f.seek(-block, 2)
            text = f.read().decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-n:])
    except Exception as e:
        return f"(tail failed: {e})"


def _now_iso() -> str:
    return datetime.now(_CET).isoformat(timespec="seconds")


def _recent_agent_runs(limit: int = 30) -> list[dict]:
    from agents._lib.runs_db import recent_runs
    try:
        return recent_runs(limit=limit)
    except Exception as e:
        return [{"error": str(e)}]


def build_bug_watcher_context() -> str:
    """Hourly log scan: bot.log + bot.err + recent agent runs.

    Context trimmed 2026-05-26 per audit: log 300→100, err 100→50,
    agent_runs 30→15. Cache-hit reduces input cost but output reasoning
    scales with input → less = cheaper response too.
    """
    log_tail = _tail(_LOG_PATH, 100)
    err_tail = _tail(_ERR_PATH, 50)
    runs = _recent_agent_runs(15)
    runs_summary = [
        {"ts": r.get("ts"), "agent": r.get("agent"), "mode": r.get("mode"),
         "duration_ms": r.get("duration_ms"), "exit_code": r.get("exit_code"),
         "error": r.get("error")}
        for r in runs
    ]
    return (
        f"# Scan-Window: last hour (now={_now_iso()})\n\n"
        f"## bot.log tail (last 300 lines)\n```\n{log_tail}\n```\n\n"
        f"## bot.err tail (last 100 lines)\n```\n{err_tail}\n```\n\n"
        f"## agent_runs.db recent (last 30)\n```json\n{json.dumps(runs_summary, indent=2, default=str)}\n```\n"
    )


def _ps_snapshot() -> str:
    try:
        out = subprocess.run(
            ["ps", "-ax", "-o", "pid,ppid,pgid,etime,command"],
            capture_output=True, text=True, timeout=5,
        )
        lines = [l for l in out.stdout.splitlines()
                 if any(k in l for k in ("main.py", "caffeinate", "run.sh", "PID"))]
        return "\n".join(lines[:30])
    except Exception as e:
        return f"(ps failed: {e})"


def _heartbeat_age() -> dict:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT body FROM kv_state WHERE namespace='runtime' AND key='heartbeat'",
            ).fetchone()
        if not row:
            return {"present": False}
        body = json.loads(row[0])
        # heartbeat schema: {"last_tick": "<ISO-naive CET>", "market_hours": ...,
        # "api_calls_today": N, ...}. Parse the string back to epoch.
        last_tick_iso = body.get("last_tick")
        if not last_tick_iso:
            return {"present": True, "last_tick": None,
                    "raw_keys": list(body.keys())}
        try:
            tick_dt = datetime.fromisoformat(last_tick_iso).replace(tzinfo=_CET)
            age_s = (datetime.now(_CET).timestamp() - tick_dt.timestamp())
        except Exception as e:
            return {"present": True, "last_tick": last_tick_iso,
                    "parse_error": str(e)}
        return {"present": True, "last_tick": last_tick_iso,
                "age_seconds": int(age_s),
                "market_hours": body.get("market_hours"),
                "api_calls_today": body.get("api_calls_today")}
    except Exception as e:
        return {"error": str(e)}


def _db_integrity() -> dict:
    out = {}
    import sqlite3
    from contextlib import closing
    for label, db_file in [("bot.db", _BOT_DIR / "state/bot.db"),
                            ("agent_runs.db", _BOT_DIR / "state/agent_runs.db")]:
        if not db_file.exists():
            out[label] = "missing"
            continue
        try:
            with closing(sqlite3.connect(db_file, timeout=5)) as conn:
                res = conn.execute("PRAGMA integrity_check").fetchone()
                out[label] = res[0] if res else "no-result"
        except Exception as e:
            out[label] = f"error: {e}"
    return out


def _open_trades_summary() -> dict:
    try:
        from core.portfolio.io import load_portfolio
        p = load_portfolio()
        open_trades = p.get("open_trades", [])
        if not open_trades:
            return {"count": 0}
        earliest = min((t.get("entry_date", "") for t in open_trades), default="")
        return {"count": len(open_trades), "earliest_entry_date": earliest,
                "tickers": [t.get("ticker") for t in open_trades]}
    except Exception as e:
        return {"error": str(e)}


def _recent_traces(limit: int = 10) -> list[dict]:
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT key, body FROM kv_state WHERE namespace='trace' "
                "ORDER BY key DESC LIMIT ?", (limit,),
            ).fetchall()
        out = []
        for k, body in rows:
            try:
                b = json.loads(body)
                out.append({"key": k, "mode": b.get("mode"),
                            "warnings": (b.get("warnings") or [])[:3]})
            except Exception:
                out.append({"key": k, "parse_error": True})
        return out
    except Exception as e:
        return [{"error": str(e)}]


def _gate_fnr_safe() -> dict:
    try:
        from core.llm.telemetry.outcomes import gate_false_negative_rates
        return gate_false_negative_rates()
    except Exception as e:
        return {"error": str(e)}


def build_health_inspector_context() -> str:
    sections = {
        "ps_snapshot": _ps_snapshot(),
        "heartbeat": _heartbeat_age(),
        "db_integrity": _db_integrity(),
        "open_trades": _open_trades_summary(),
        "recent_traces": _recent_traces(10),
        "gate_fnr": _gate_fnr_safe(),
        "agent_runs_recent": _recent_agent_runs(50),
    }
    # Context-trim per 2026-05-26 audit: log 200→80, err 100→40,
    # agent_runs 50→20. Less input → less output reasoning → cheaper.
    log_tail = _tail(_LOG_PATH, 80)
    err_tail = _tail(_ERR_PATH, 40)
    return (
        f"# Health snapshot (now={_now_iso()})\n\n"
        f"## ps -ax (filtered)\n```\n{sections['ps_snapshot']}\n```\n\n"
        f"## Heartbeat\n```json\n{json.dumps(sections['heartbeat'], indent=2, default=str)}\n```\n\n"
        f"## DB integrity\n```json\n{json.dumps(sections['db_integrity'], indent=2)}\n```\n\n"
        f"## Open trades\n```json\n{json.dumps(sections['open_trades'], indent=2, default=str)}\n```\n\n"
        f"## Recent brain traces\n```json\n{json.dumps(sections['recent_traces'], indent=2, default=str)}\n```\n\n"
        f"## Gate-FNR (deterministic monitoring)\n```json\n{json.dumps(sections['gate_fnr'], indent=2, default=str)[:1500]}\n```\n\n"
        f"## bot.log tail (80)\n```\n{log_tail}\n```\n\n"
        f"## bot.err tail (40)\n```\n{err_tail}\n```\n\n"
        f"## agent_runs.db recent (20)\n```json\n{json.dumps(sections['agent_runs_recent'][:20], indent=2, default=str)[:2000]}\n```\n"
    )


def _closed_today() -> list[dict]:
    try:
        from core.portfolio.closed_trades_store import load_closed_trades
        all_closed = load_closed_trades()
        today_str = datetime.now(_CET).strftime("%Y-%m-%d")
        return [
            t for t in all_closed
            if str(t.get("closed_at", "")).startswith(today_str)
        ]
    except Exception as e:
        return [{"error": str(e)}]


def _existing_research_index() -> list[str]:
    if not _RESEARCH_DIR.exists():
        return []
    return sorted([p.name for p in _RESEARCH_DIR.glob("*.md")])


def build_eod_postmortem_context() -> str:
    closed = _closed_today()
    if not closed:
        return f"# EOD-Postmortem ({_now_iso()})\n\nNo trades closed today. Respond with 'NOTHING_TO_REVIEW'."
    research_idx = _existing_research_index()
    return (
        f"# EOD-Postmortem ({_now_iso()})\n\n"
        f"## closed_today ({len(closed)} trades)\n```json\n{json.dumps(closed, indent=2, default=str)}\n```\n\n"
        f"## existing_research_index (don't overwrite)\n```\n{chr(10).join(research_idx)}\n```\n"
    )


def build_weekly_calibrator_context() -> str:
    try:
        from core.portfolio.io import load_portfolio
        from core.portfolio.hit_stats import compute_hit_stats
        from core.llm.telemetry.outcomes import gate_false_negative_rates

        p = load_portfolio()
        hit = compute_hit_stats(p)
        fnr = gate_false_negative_rates()
        closed = p.get("closed_trades", [])
        recent_30d = closed[-50:] if closed else []
    except Exception as e:
        return f"# Weekly-Calibrator ({_now_iso()})\n\nContext-gather failed: {e}"

    return (
        f"# Weekly-Calibrator ({_now_iso()})\n\n"
        f"## hit_stats\n```json\n{json.dumps(hit, indent=2, default=str)[:3000]}\n```\n\n"
        f"## gate_fnr\n```json\n{json.dumps(fnr, indent=2, default=str)[:2000]}\n```\n\n"
        f"## closed_trades (last 50)\n```json\n{json.dumps(recent_30d, indent=2, default=str)[:5000]}\n```\n"
    )


def _file_index_with_head(dir_path: Path, n_chars: int = 200) -> list[dict]:
    if not dir_path.exists():
        return []
    out = []
    for p in sorted(dir_path.glob("*.md")):
        try:
            head = p.read_text(encoding="utf-8")[:n_chars]
        except Exception as e:
            head = f"(read error: {e})"
        out.append({"name": p.name, "head": head})
    return out


def _git_log_30d() -> str:
    try:
        out = subprocess.run(
            ["git", "log", "--since=30 days ago", "--pretty=%h %s"],
            cwd=_REPO_DIR, capture_output=True, text=True, timeout=10,
        )
        return out.stdout[:5000]
    except Exception as e:
        return f"(git log failed: {e})"


def build_backlog_keeper_context() -> str:
    incidents = _file_index_with_head(_INCIDENTS_DIR)
    research = _file_index_with_head(_RESEARCH_DIR)
    tuning = [{"name": p.name} for p in _TUNING_DIR.glob("tuning-suggestions-*.md")]
    backlog_current = _BACKLOG_PATH.read_text(encoding="utf-8") if _BACKLOG_PATH.exists() else ""
    git_log = _git_log_30d()
    open_trades = _open_trades_summary()

    return (
        f"# Backlog-Keeper ({_now_iso()})\n\n"
        f"## incidents_dir_listing\n```json\n{json.dumps(incidents, indent=2)[:4000]}\n```\n\n"
        f"## research_dir_listing\n```json\n{json.dumps(research, indent=2)[:4000]}\n```\n\n"
        f"## tuning_dir_listing\n```json\n{json.dumps(tuning, indent=2)}\n```\n\n"
        f"## current_backlog\n```markdown\n{backlog_current[:4000]}\n```\n\n"
        f"## git_log_30d\n```\n{git_log}\n```\n\n"
        f"## current_open_trades\n```json\n{json.dumps(open_trades, indent=2, default=str)}\n```\n"
    )


CONTEXT_BUILDERS = {
    "bug-watcher": build_bug_watcher_context,
    "health-inspector": build_health_inspector_context,
    "eod-postmortem": build_eod_postmortem_context,
    "weekly-calibrator": build_weekly_calibrator_context,
    "backlog-keeper": build_backlog_keeper_context,
}
