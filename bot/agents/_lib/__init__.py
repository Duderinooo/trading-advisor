"""Agent runtime: subprocess-based Claude CLI invocation.

The bot pays per API call against the Anthropic API. The same Claude models are
free-at-the-margin via the Claude Code subscription (Pro/MAX) when invoked
through the `claude` CLI. This package routes LLM calls through the CLI when
the `USE_AGENTS` feature flag is on, falling back to the API otherwise.
"""

from agents._lib.runner import call_claude_agent, AgentRunError
from agents._lib.runs_db import init_runs_db, log_run, recent_runs

__all__ = [
    "call_claude_agent", "AgentRunError",
    "init_runs_db", "log_run", "recent_runs",
]
