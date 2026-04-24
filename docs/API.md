# Trading Advisor — User & Operations Doc

Alles was User braucht: Start, Stop, Commands, Config, Risk-Gates, Troubleshooting.

---

## 1. Bot starten / stoppen

### Start (Dauerbetrieb, empfohlen)

```bash
cd ~/trading-advisor
./run.sh
```

- Aktiviert venv, startet `main.py`, hält Mac via `caffeinate -is` wach
- Log läuft in `bot.log` / `bot.err` (bzw. stdout wenn Foreground)
- `Ctrl-C` → sauberer Shutdown

### Start im Hintergrund

```bash
nohup ./run.sh > bot.log 2> bot.err &
echo $! > bot.pid           # PID merken für späteres Stoppen
```

### Stoppen

```bash
kill $(cat bot.pid)         # wenn nohup genutzt
# oder
pkill -f "main.py"          # hart
```

### Status prüfen

```bash
ps aux | grep main.py       # läuft?
tail -f bot.log             # live-log
```

### Dev / manueller Einzellauf

```bash
source venv/bin/activate
python main.py
```

---

## 2. Telegram-Befehle

Alle Commands werden im **Chat mit deinem Bot** gesendet. Nur die in `TELEGRAM_CHAT_ID` konfigurierte Chat-ID wird akzeptiert.

### `/confirm` — Empfehlung ausführen

Übernimmt eine Entry-Empfehlung in dein Portfolio (nachdem du den Trade manuell auf Trade Republic platziert hast).

| Form | Bedeutung |
|------|-----------|
| `/confirm` *(als Reply auf Bot-Empfehlung)* | Nutze Rec-Werte (Preis, Size → auto-Shares) |
| `/confirm 3` *(Reply)* | 3 Stück zum Rec-Preis |
| `/confirm 0.55` *(Reply)* | TR Bruchstück 0.55 Stück |
| `/confirm 3 @172.50` *(Reply)* | 3 Stück, tatsächlicher Fill-Preis €172.50 |
| `/confirm NVD.DE 3 @172.50` | Standalone, neueste pending Rec für NVD.DE |

**Blocker:**
- Risk-Halt aktiv (Daily-Loss/DD/Heat) → verweigert mit Begründung
- Kein gültiger SL auf Rec → verweigert (Ruin-Schutz)
- Cash nicht ausreichend → zeigt max möglich

### `/close TICKER [@preis]` — Position schließen

```
/close NVD.DE              # Exit zum aktuellen Marktpreis (yfinance)
/close NVD.DE @175.20      # Exit zum tatsächlichen Fill
```

Buchung: P&L berechnet, `closed_trades` aktualisiert, Cash zurück, Brier-Score für Kalibrierung.

### `/cancel` — Pending-Empfehlung verwerfen

```
/cancel                    # als Reply auf Rec-Message
/cancel NVD.DE             # neueste pending Rec für Ticker
```

### `/positions` — Portfolio anzeigen

Zeigt: Cash, offene Positionen (Ticker, Shares, Entry, SL, TP), pending Recs.

### `/help` — Befehlsübersicht

---

## 3. Bot-Verhalten (was läuft automatisch)

| Zeit (CET) | Trigger | Was passiert |
|---|---|---|
| **08:00** werktags | `MORNING_PREP_HOUR` | Claude Sonnet Morning-Brief, setzt Watch-Levels, kann Rec generieren |
| **09:05** werktags | XETRA-Open +5min | Opening-Check (nur bei Gaps/Action) |
| **15:35** werktags | US-Open +5min | US-Opening-Check |
| **alle 15min** | `PRICE_CHECK_INTERVAL_MINUTES` | Preis-Poll, SL/TP-Enforcement, Event-Detection, News-Scan |
| **event-driven** | Big move / breakout / news | Haiku-Analyse triggert |

**Nie automatisch ausgeführt:** Orders. Bot schickt nur Signale → User tradet auf TR → `/confirm`.

---

## 4. Risk-Management

Alle Werte in `config.py`, Abschnitt "Risk management" + "Circuit breakers".

### Risk-Config

| Variable | Default | Zweck |
|---|---|---|
| `BUDGET_EUR` | 1000 | Startkapital |
| `MAX_RISK_PER_TRADE_PERCENT` | 3.0 | Max % Kapital pro Trade riskiert (Entry→SL) |
| `MAX_POSITION_SIZE_PERCENT` | 8.0 | Hard-Cap: max 8% Kapital in einzelner Position |
| `MIN_CASH_RESERVE_PERCENT` | 20.0 | Cash-Reserve immer halten |
| `MAX_ACTIVE_TRADES` | 5 | Max offene Positionen |
| `MAX_TRADES_PER_DAY` | 2 | Max neue Trades/Tag |
| `MAX_POSITIONS_PER_SECTOR` | 2 | Sector-Cluster-Protection |
| `DAILY_LOSS_HALT_PERCENT` | 5.0 | Tages-P&L ≤ -5% → Rest-Tag PASS-only |
| `DRAWDOWN_HALT_PERCENT` | 8.0 | Equity ≤ Peak × 0.92 → PASS-only |
| `DRAWDOWN_RECOVERY_PERCENT` | 2.0 | Halt endet nach +2% Recovery |
| `MAX_PORTFOLIO_HEAT_PERCENT` | 10.0 | Summe aller (Entry−SL) ≤ 10% Kapital |
| `MIN_EXPECTED_EDGE` | 0.04 | `p·b − (1−p) ≥ 0.04` sonst PASS erzwungen |
| `KELLY_FRACTION` | 0.25 | Quarter-Kelly Skalierung für Sizing |

### Circuit-Breakers (was Bot blockiert)

**Entry-Blocker** (greifen bei Claude-Rec vor Posting an User):

1. **Daily-Loss-Cap** — realisierter P&L heute ≤ −5% Kapital → kein neuer Entry bis 00:00
2. **Drawdown-Halt** — Equity unter 92% des Peak → kein neuer Entry bis Recovery
3. **Portfolio-Heat** — Summe der Stop-Risks aller offenen Positionen ≥ 10% → kein neuer Entry
4. **Edge-Gate** — `p_win·R + (1−p_win)·(−1) < 4%` → PASS erzwungen
5. **Kein SL** — Rec ohne gültigen Stop-Loss → blockiert

**Confirm-Blocker** (greifen bei `/confirm`):

- Halt-Status wird neu evaluiert (State kann sich seit Rec geändert haben)
- SL muss auf Rec existieren, > 0, < Entry
- Cash muss reichen

### Position-Sizing-Logik

Bot berechnet Position-EUR = `min(ATR-size, Kelly-size, Hard-Cap)`:

- **ATR-size** = `Kapital × 3% / (1.5 × ATR14%)` — volatilitäts-normalisiertes Risk
- **Kelly-size** = `Kapital × f* × 0.25` mit `f* = (p·b − (1−p)) / b`, nur wenn `p_win` + `reward_to_risk` vorliegen
- **Hard-Cap** = `Kapital × 8%`

Negative Edge → size = 0 → kein Trade.

---

## 5. Portfolio-State-Struktur

`portfolio.json` (vom Bot verwaltet):

```json
{
  "open_trades": [
    {
      "ticker": "NVD.DE",
      "entry_price": 171.20,
      "shares": 0.55,
      "size_eur": 94.16,
      "stop_loss": 165.00,
      "take_profit": [180.00, 190.00],
      "trailing_stop_pct": 5.0,
      "conviction": 4,
      "p_win": 0.62,
      "entry_date": "2026-04-24 10:15",
      "status": "open"
    }
  ],
  "closed_trades": [ /* gleiche Shape + exit_price, pnl_eur, pnl_pct, brier, outcome */ ],
  "pending_recommendations": [ /* warten auf /confirm */ ],
  "watch_levels": [ /* Bot-gesetzte Breakout-Level */ ],
  "cash_eur": 905.84,
  "total_capital_eur": 1000.00,
  "last_updated": "2026-04-24 10:15",
  "last_morning_prep_date": "2026-04-24"
}
```

Atomic Save: tmp-file + `os.replace` → crash-safe. Reload-merge unter Lock → keine verlorenen Writes zwischen Main-Loop + Telegram-Thread.

---

## 6. Logs & Debugging

### Log-Files

- `bot.log` — stdout (INFO level Events, Analysen)
- `bot.err` — stderr (Exceptions, Warnings)

### Typische Log-Marker

```
Entry recommendation: NVD.DE @ €171.20 (msg_id=1234)
Entry BLOCKED by risk halt: Daily-Loss-Cap: -5.23% ≤ -5.0%
Entry BLOCKED by edge gate: edge=0.021 < 0.040 (p_win=0.52)
Watch levels updated: 3 level(s) registered
SL HIT: NVD.DE @ €165.00 → closed
```

### Portfolio inspizieren

```bash
cat portfolio.json | jq .       # human-readable
cat portfolio.json | jq '.open_trades'
cat portfolio.json | jq '.closed_trades | length'
```

### Rate-Limit-Status

```bash
grep "API usage" bot.log | tail
```

---

## 7. Troubleshooting

| Problem | Check |
|---|---|
| Bot sendet keine Nachrichten | Bot in Telegram angeschrieben? `/start` gesendet? `TELEGRAM_CHAT_ID` korrekt? |
| "Telegram listener NOT started" | `TELEGRAM_BOT_TOKEN` oder `TELEGRAM_CHAT_ID` fehlt in `.env` |
| Keine Recs trotz Events | Risk-Halt aktiv? `bot.log` nach "BLOCKED" grepen |
| `/confirm` verweigert mit "Kein SL" | Rec hatte keinen Stop-Loss — alte Rec vor Gate-Update. Neue Analyse abwarten |
| "Nicht genug Cash" | `/positions` → Cash prüfen. Rec size_eur > verfügbar |
| yfinance-Fehler | Netzwerk? Rate-Limit? Market-Daten cachen 60s |
| Bot hängt bei Start | venv aktiv? `pip install -r requirements.txt` |

### Reset / Wipe

```bash
cp portfolio.json portfolio.backup.json   # backup zuerst!
# State editieren: cash_eur, open_trades zurücksetzen
```

**Nie** `closed_trades` löschen — Kalibrierung (Brier-Score) braucht Historie.

---

## 8. Config-Cheatsheet

Häufig angepasste Werte in `config.py`:

```python
BUDGET_EUR = 1000.0                   # Startkapital
WATCHLIST = ["NVD.DE", "APC.DE", ...] # Beobachtete Tickers
EXCLUDED_TICKERS = ["PTX.DE", ...]    # Nicht traden (Sparpläne)
MAX_POSITION_SIZE_PERCENT = 8.0       # Hard-Cap pro Position
DAILY_LOSS_HALT_PERCENT = 5.0         # Tages-Stop
DRAWDOWN_HALT_PERCENT = 8.0           # DD-Stop
MIN_EXPECTED_EDGE = 0.04              # Min Edge für Entry
CLAUDE_MODEL_MORNING = "claude-sonnet-4-6"
CLAUDE_MODEL_EVENT = "claude-haiku-4-5"
```

Nach Änderung: Bot neu starten.

---

## 9. MemPalace (optional)

Wenn `mempalace` MCP konfiguriert: Bot schreibt Trades + Analysen automatisch als Knowledge-Graph-Nodes. Deaktiviert wenn nicht verfügbar, kein Error.

Check:
```bash
grep MEMPALACE bot.log | head
```

---

## 10. Sicherheitshinweise

- `.env` **nie** committen (enthält API-Keys)
- `portfolio.json` enthält P&L-Historie — privat halten
- Bot akzeptiert nur Commands von `TELEGRAM_CHAT_ID` — andere Chats werden ignoriert
- Claude-API-Calls sind rate-limited (20/Tag default) → Kosten gedeckelt
