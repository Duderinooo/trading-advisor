# 2026-05-02 — News dedup overwrite at midnight rollover

## Symptom

News articles from the previous day re-fired as new events the next morning.
RSS feeds keep 24h-old stories in the feed; bot's `seen_news` dedup-set was
wiped at midnight, so stories from 23:30 the day before triggered fresh
analyses at 00:01.

## Root cause

`check_news_events` persisted `seen_news` as `{today: list(hashes)}` —
overwriting previous-day entries instead of keeping yesterday alongside today.

```python
# OLD
portfolio["seen_news"] = {today: list(seen_today)}  # WIPED yesterday
```

When a story published 23:00 was hashed + saved under date=2026-05-01, the
midnight rollover replaced the entire dict with date=2026-05-02. The story
still showed up in Google News RSS at 00:30 → matched no hash → emitted event.

## Fix

Keep both today + yesterday in `seen_news`:

```python
yesterday = str(date.today() - timedelta(days=1))
existing = portfolio.get("seen_news", {}) or {}
portfolio["seen_news"] = {
    today: list(seen_today),
    yesterday: list(existing.get(yesterday, [])),
}
```

Pruning beyond 2 days happens at startup_cleanup.

## Constants

None — pure logic fix.

## Lessons

- **Per-day dedup-sets must overlap with previous day** when sources keep
  24h-old items (RSS especially).
- **Rollover edges** are silent bug-magnets. Test cases should cover the
  23:55–00:05 window explicitly.
- **Don't overwrite multi-key dicts** when merging — use `.update()` or
  explicit construction.
