# Git hooks (local-only)

## pre-commit

Advisory tuning audit when staged changes touch `config/`. Runs
`tools.tuning_audit --staged --days 30` and prints the replay P&L delta for
any detected numeric constant change. **Never blocks the commit** — purely
informational.

## Install (one-time per clone)

```bash
git config core.hooksPath .githooks
```

This redirects git's hook lookup to `.githooks/` (tracked in repo) instead of
`.git/hooks/` (per-clone untracked).

## Bypass

If a particular commit shouldn't trigger the audit (e.g. a comment-only doc
change in config/), use `git commit --no-verify`. Don't habitualize — the
whole point is to surface tuning impact at decision time.
