"""Bug-worker orchestrator.

Picks one unresolved incident from `docs/incidents/`, creates a fix branch,
invokes the bug-worker skill agent with elevated tool access, parses the
status block, runs tests as a final safety check, commits + optionally pushes.

Safety constraints (hard-coded):
- Never edits on `main` — always creates `fix/incident-<ts>` branch
- Tests must pass before any commit
- Push to remote is opt-in via `AGENT_BUG_WORKER_PUSH=1` env var
- One incident per run — caller (scheduler) loops as needed
- Skips incidents already marked `processed: true` in frontmatter
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents._lib.runner import _resolve_claude_bin
from agents._lib.runs_db import log_run

logger = logging.getLogger(__name__)

_BOT_DIR = Path(__file__).resolve().parents[2]
_REPO_DIR = _BOT_DIR.parent
_INCIDENTS_DIR = _REPO_DIR / "docs" / "incidents"
_SKILL_PATH = _BOT_DIR / "agents" / "skills" / "bug-worker.md"
_CET = ZoneInfo("Europe/Berlin")
_STATUS_FRONTMATTER = "<!-- bug-worker-status: processed -->"


@dataclass
class WorkerResult:
    ok: bool
    incident_name: str | None
    status: str  # "fixed" | "deferred" | "failed" | "noop" | "error"
    commit_sha: str | None = None
    branch: str | None = None
    pushed: bool = False
    summary: str = ""


def _git(*args: str, cwd: Path = _REPO_DIR) -> subprocess.CompletedProcess:
    """Run git command, returning CompletedProcess (capture stdout/stderr)."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30,
    )


def _on_main() -> bool:
    out = _git("symbolic-ref", "--short", "HEAD")
    branch = out.stdout.strip()
    return branch == "main"


def _working_tree_clean() -> bool:
    out = _git("status", "--porcelain")
    return out.stdout.strip() == ""


def _next_incident() -> Path | None:
    if not _INCIDENTS_DIR.exists():
        return None
    for p in sorted(_INCIDENTS_DIR.glob("*-bug-watcher.md")):
        content = p.read_text(encoding="utf-8", errors="replace")
        if _STATUS_FRONTMATTER not in content:
            return p
    return None


def _mark_processed(incident_path: Path, status: str, extra: str = "") -> None:
    """Append bug-worker-status marker so next run skips."""
    body = incident_path.read_text(encoding="utf-8")
    marker = f"\n\n{_STATUS_FRONTMATTER} status={status} ts={_ts_compact()} {extra}\n"
    if _STATUS_FRONTMATTER not in body:
        incident_path.write_text(body.rstrip() + marker, encoding="utf-8")


def _ts_compact() -> str:
    return datetime.now(_CET).strftime("%Y-%m-%d-%H%M")


def _run_tests() -> tuple[bool, str]:
    """Returns (passed, last_lines_of_output)."""
    try:
        out = subprocess.run(
            [str(_BOT_DIR.parent / "venv" / "bin" / "python"),
             "-m", "unittest", "discover", "-s", "tests"],
            cwd=_BOT_DIR, capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    combined = out.stdout + out.stderr
    last = "\n".join(combined.splitlines()[-5:])
    passed = "OK" in combined and out.returncode == 0
    return passed, last


def _parse_status_block(text: str) -> dict | None:
    """Extract the STATUS block from the worker's output."""
    m = re.search(r"STATUS:\s*(fixed|deferred|failed)", text)
    if not m:
        return None
    status = m.group(1)
    out: dict = {"status": status}
    for key in ("COMMIT_TITLE", "COMMIT_BODY", "FILES_CHANGED", "TESTS_PASS",
                "INCIDENT_RESOLVED", "REASON", "TEST_OUTPUT"):
        m2 = re.search(rf"{key}:\s*(.+?)(?=\n[A-Z_]+:|\n```|\Z)",
                       text, re.DOTALL)
        if m2:
            out[key.lower()] = m2.group(1).strip()
    return out


def _invoke_bug_worker(incident_path: Path, incident_body: str,
                       branch_name: str) -> tuple[bool, str, int]:
    """Run `claude -p` with Edit/Bash/Read tools. Returns (ok, stdout, duration_ms)."""
    claude_bin = _resolve_claude_bin()
    system_prompt = _SKILL_PATH.read_text(encoding="utf-8")

    user_msg = (
        f"# Incident to fix\n\n"
        f"incident_path: {incident_path.relative_to(_REPO_DIR)}\n"
        f"branch_name: {branch_name}\n"
        f"repo_root: {_REPO_DIR}\n\n"
        f"## incident_body\n\n{incident_body}\n"
    )

    cmd = [
        claude_bin, "-p",
        "--model", "sonnet",
        "--output-format", "json",
        "--no-session-persistence",
        "--append-system-prompt", system_prompt,
        "--allowedTools", "Read Edit Write Grep Glob Bash",
        "--permission-mode", "acceptEdits",
        "--add-dir", str(_REPO_DIR),
        user_msg,
    ]
    # Strip API key so CLI uses subscription path.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=_REPO_DIR, capture_output=True, text=True,
            timeout=600, env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT", int((time.time() - t0) * 1000)
    duration_ms = int((time.time() - t0) * 1000)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout)[:1000], duration_ms

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, proc.stdout[:1000], duration_ms
    text = envelope.get("result") or ""
    return True, text, duration_ms


def run_once() -> WorkerResult:
    """Pick + fix one incident. Returns WorkerResult.

    Refuses to start if:
      - currently on main branch (we MUST start from a clean branch)
      - working tree is dirty (would mix with worker edits)
      - no unprocessed incidents
    """
    if not _SKILL_PATH.exists():
        return WorkerResult(False, None, "error",
                            summary="bug-worker.md skill missing")
    # 2026-05-27: dirty-tree + not-on-main are EXPECTED dev-time states,
    # not errors. Return noop (ok=True, no Telegram alert) so the scheduler
    # silently skips. Was spamming user every 30min during interactive
    # sessions when an unrelated uncommitted edit existed.
    if not _working_tree_clean():
        return WorkerResult(True, None, "noop",
                            summary="working tree dirty — skipping (silent)")
    if not _on_main():
        return WorkerResult(True, None, "noop",
                            summary="not on main — skipping (silent)")

    incident_path = _next_incident()
    if incident_path is None:
        return WorkerResult(True, None, "noop", summary="no incidents pending")

    name = incident_path.name
    incident_body = incident_path.read_text(encoding="utf-8")
    branch = f"fix/incident-{_ts_compact()}"

    # Create + checkout fix branch from main.
    co = _git("checkout", "-b", branch)
    if co.returncode != 0:
        return WorkerResult(False, name, "error",
                            summary=f"branch create failed: {co.stderr[:200]}")

    try:
        ok, worker_text, dur_ms = _invoke_bug_worker(
            incident_path, incident_body, branch,
        )
        log_run(f"skill:bug-worker", model="sonnet",
                duration_ms=dur_ms, exit_code=0 if ok else 1,
                output_size=len(worker_text),
                meta={"incident": name, "branch": branch})

        if not ok:
            _git("checkout", "main")
            _git("branch", "-D", branch)
            _mark_processed(incident_path, "error",
                            f"invocation failed: {worker_text[:80]}")
            return WorkerResult(False, name, "error", branch=branch,
                                summary=f"worker invocation failed: {worker_text[:200]}")

        parsed = _parse_status_block(worker_text)
        if not parsed:
            _git("checkout", "main")
            _git("branch", "-D", branch)
            _mark_processed(incident_path, "error", "no status block")
            return WorkerResult(False, name, "error", branch=branch,
                                summary="worker returned no STATUS block")

        status = parsed["status"]
        if status in ("deferred", "failed"):
            _git("reset", "--hard", "HEAD")
            _git("checkout", "main")
            _git("branch", "-D", branch)
            _mark_processed(incident_path, status, parsed.get("reason", ""))
            return WorkerResult(True, name, status, branch=branch,
                                summary=parsed.get("reason", "no reason"))

        # status == "fixed". Verify working tree actually changed.
        diff = _git("diff", "--name-only")
        changed_files = [l for l in diff.stdout.splitlines() if l.strip()]
        if not changed_files:
            _git("checkout", "main")
            _git("branch", "-D", branch)
            _mark_processed(incident_path, "noop",
                            "worker claimed fixed but no diff")
            return WorkerResult(True, name, "noop", branch=branch,
                                summary="worker claimed fixed but no diff")

        # Final test gate (the skill should have run them, but verify).
        passed, test_tail = _run_tests()
        if not passed:
            _git("reset", "--hard", "HEAD")
            _git("checkout", "main")
            _git("branch", "-D", branch)
            _mark_processed(incident_path, "test-failed", test_tail[:80])
            return WorkerResult(False, name, "failed", branch=branch,
                                summary=f"tests failed after worker edits:\n{test_tail}")

        # Commit.
        title = parsed.get("commit_title", "fix(bot): bug-worker autonomous fix")
        body = parsed.get("commit_body", "").strip()
        msg = title
        if body:
            msg += "\n\n" + body
        msg += "\n\nIncident: " + str(incident_path.relative_to(_REPO_DIR))
        msg += "\n\nCo-Authored-By: Claude bug-worker <noreply@anthropic.com>"
        _git("add", "-A")
        commit = _git("commit", "-m", msg)
        if commit.returncode != 0:
            _git("checkout", "main")
            _git("branch", "-D", branch)
            _mark_processed(incident_path, "commit-failed",
                            commit.stderr[:80])
            return WorkerResult(False, name, "error", branch=branch,
                                summary=f"commit failed: {commit.stderr[:200]}")

        sha = _git("rev-parse", "HEAD").stdout.strip()[:7]

        pushed = False
        if os.environ.get("AGENT_BUG_WORKER_PUSH") == "1":
            push = _git("push", "-u", "origin", branch)
            pushed = push.returncode == 0

        # Return to main branch — worker is done with this fix branch.
        _git("checkout", "main")
        _mark_processed(incident_path, "fixed",
                        f"branch={branch} sha={sha}")

        return WorkerResult(
            True, name, "fixed",
            commit_sha=sha, branch=branch, pushed=pushed,
            summary=f"{title}\n\nfiles: {parsed.get('files_changed','?')}",
        )

    except Exception as e:
        logger.exception("bug-worker crashed")
        # Best-effort cleanup.
        try:
            _git("reset", "--hard", "HEAD")
            _git("checkout", "main")
            _git("branch", "-D", branch)
        except Exception:
            pass
        return WorkerResult(False, name, "error",
                            summary=f"crashed: {e}")
