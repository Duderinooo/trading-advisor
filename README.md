# Trading Advisor Bot

Claude-powered swing-trading advisor. Monitors XETRA/US markets, sends entry/exit signals via Telegram. User executes 1:1 on Trade Republic.

**Full-trust model:** Bot = sole filter. Every rec designed for direct execution. Strikte Risk-Gates (daily-loss cap, drawdown halt, edge gate, hard position cap 8%) verhindern Ruin.

## Bedienung — Quick Reference

Kompletter Command-Katalog → [docs/API.md](docs/API.md)

```bash
./run.sh                         # Bot starten (mit caffeinate, bleibt wach)
./venv/bin/python main.py        # Manueller Start ohne Wake-Lock
```

Telegram (nach Start):
- `/positions` — Portfolio + Pending
- `/confirm` (reply) — Empfehlung übernehmen
- `/close TICKER` — Position schließen
- `/cancel` (reply) — Pending verwerfen
- `/help` — alle Befehle

## Features

- Claude Sonnet morning-brief + Haiku event-checks
- Telegram signals (Entry, SL/TP, Watch-Levels, News-Triggers)
- Price alerts (drop/rise, gap, breakout)
- Risk-Gates: 5% Daily-Loss-Cap, 8% Drawdown-Halt, 4% Edge-Gate, 8% Max-Position, 10% Portfolio-Heat
- Quarter-Kelly Sizing (optional, wenn p_win + R:R vorliegen)
- Sector-Exposure Cap (max 2 pro Sektor)
- MemPalace-Integration (Trade-Historie als Knowledge-Graph)
- Atomic Portfolio-State (crash-safe via tmp+rename)

## Setup

### 1. Dependencies

```bash
cd ~/trading-advisor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Env-Vars

`.env` im Projekt-Root:

```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
```

- **Anthropic:** [console.anthropic.com](https://console.anthropic.com) → API Key
- **Telegram Bot:** `@BotFather` → `/newbot` → Token kopieren
- **Chat-ID:** `@userinfobot` → `/start` → ID kopieren
- Nach Bot-Create: eigenen Bot anschreiben, `/start` senden (sonst kann Bot nicht antworten)

### 3. Portfolio

`portfolio.json` — initial shape:

```json
{
  "open_trades": [],
  "closed_trades": [],
  "cash_eur": 1000.0,
  "total_capital_eur": 1000.0
}
```

Bot verwaltet den Rest (pending_recommendations, watch_levels, equity-peak, ...) selbst.

### 4. Watchlist / Risk-Config

`config.py`:
- `BUDGET_EUR` — Startkapital
- `WATCHLIST` — XETRA-Tickers (EUR-Preise für TR)
- `EXCLUDED_TICKERS` — Sparpläne / nicht traden
- Risk-Knobs: siehe [docs/API.md](docs/API.md#risk-config)

## Running

### Dauerbetrieb (macOS, bleibt wach)

```bash
./run.sh
```

`caffeinate -is` verhindert Sleep solange Prozess läuft. `Ctrl-C` → clean shutdown.

### Background

```bash
nohup ./run.sh > bot.log 2> bot.err &
```

### Dev / Manueller Start

```bash
source venv/bin/activate
python main.py
```

### Tests / One-Off

```bash
python notifier.py               # Telegram-Verbindung testen
python -m core.analyzer          # (wenn __main__ vorhanden)
```

## Architektur

```
main.py               Event-Loop (market hours, morning prep, opening checks)
core/
  analyzer.py         Claude-Call Orchestrator + Risk-Gates
  portfolio.py        State I/O, Sizing, Heat, Halt-Logic
  events.py           SL/TP-Enforcement, News, Price-Alerts
  market_data.py      yfinance-Wrapper + Cache
  prompts.py          System + User-Prompts, Tool-Schemas
  api_usage.py        Rate-Limiting
telegram_listener.py  /confirm /close /positions /cancel /help
notifier.py           Telegram-Send
memory.py             MemPalace-Integration
macro.py              Econ-Calendar
```

## Risk-Management

Siehe [docs/API.md](docs/API.md#risk-management) für Details zu:
- Position-Sizing (ATR + Quarter-Kelly + Hard-Cap)
- Circuit-Breakers (Daily-Loss, Drawdown, Heat)
- Edge-Gate (Min expectancy 4%)
- Sector-Cap

## Kosten

- Claude API: ~€3-6/Monat (gemessen)
  - Morning-Call (Sonnet 4.6): ~€0.06/Tag × ~22 Trading-Tage = ~€1.30
  - Opening-Checks (Haiku 4.5, Xetra+US): ~€0.02/Tag = ~€0.45
  - Event-/News-Checks (Haiku 4.5): variabel ~€0.05-0.20/Tag
- Strom: ~€3-4/Monat (Mac 24/7)
- Telegram: free

## Disclaimer

Analyse-Tool. Keine Anlageberatung. User trägt volle Verantwortung für ausgeführte Trades. Vergangene Performance ≠ zukünftige. Nur Geld einsetzen, dessen Verlust verkraftbar.
