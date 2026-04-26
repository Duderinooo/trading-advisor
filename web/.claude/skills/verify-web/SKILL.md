---
name: verify-web
description: Run the verification recipe for the web/ project — TypeScript check + dev server smoke test + curl all routes + check for hydration warnings. Use after any code change in web/. Triggers on "verify web", "test web", "check dashboard", "smoke test", "läuft das noch".
---

# Verify web/ changes

No test suite. Manual smoke recipe — run after any non-trivial change in `web/`.

## 1. Type-check

```bash
cd /Users/malteollmann/trading-advisor/web
npx tsc --noEmit
```

Expect zero output. Any error → fix before proceeding.

## 2. Dev server starts cleanly

```bash
(npm run dev > /tmp/web-verify.log 2>&1 &)
sleep 5
grep -E "Ready|error|Error" /tmp/web-verify.log
```

Expect `✓ Ready in <ms>`, no errors.

## 3. All routes return 200

```bash
for route in / /logs /api/portfolio /api/equity /api/stats; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:3000$route")
  echo "$code  $route"
done

curl -s -o /dev/null -w "%{http_code}  /api/logs?file=bot.log\n" \
  "http://localhost:3000/api/logs?file=bot.log&lines=10"
curl -s -o /dev/null -w "%{http_code}  /api/logs?file=invalid (expect 400)\n" \
  "http://localhost:3000/api/logs?file=../../../etc/passwd"
```

Expect: all dashboard/api routes 200, the path-traversal probe 400.

## 4. Page renders expected content

```bash
curl -s http://localhost:3000/ -o /tmp/page.html
grep -E -o "(Trading Advisor|Equity Curve|Watch Levels|Open Trades|Closed Trades|1D|1W|1M|1Y|ALL)" /tmp/page.html | sort -u
```

Expect every label to appear once. Missing one means a section didn't render server-side.

## 5. APIs return real data

```bash
curl -s http://localhost:3000/api/portfolio | python3 -m json.tool | head -30
curl -s http://localhost:3000/api/equity | python3 -m json.tool | head -20
```

`open_trades`, `closed_trades`, `total_capital_eur` should be present and non-empty (assuming the bot has been run).

## 6. Hydration check (browser only — `curl` cannot do this)

Open `http://localhost:3000` in the browser and check the DevTools console:

- ❌ "Hydration failed because…" → a client component initialized state from `new Date()`, `Math.random()`, or locale-formatted text. See [../../docs/CONVENTIONS.md](../../docs/CONVENTIONS.md) "Hydration safety".
- ✅ No warnings → ship.

## 7. Stop dev server

```bash
pkill -f "next dev"
```

## When something fails

| Symptom | Where to look |
|---|---|
| `tsc` errors | The file printed in the error. Fix at the source — don't `as any`. |
| Dev server fails to start | `/tmp/web-verify.log`, often a port collision or syntax error in a config file. |
| Route 500 | Server log in `/tmp/web-verify.log` — usually a fs read failure or a missing route export. |
| Hydration warning | [../../docs/CONVENTIONS.md](../../docs/CONVENTIONS.md) — replace `new Date()` initial state, etc. |
| Wrong numbers | Compare against `core/portfolio.py::compute_hit_stats` and `_equity_curve` — [../../lib/compute.ts](../../lib/compute.ts) is a port and may have drifted. |
