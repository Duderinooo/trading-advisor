# Trading Advisor — Telegram Cheatsheet

Quick-Reference. Vollständige Logik in [../CLAUDE.md](../CLAUDE.md).

## Du tippst

### Trade-Ausführung (Reaktion auf Bot-Empfehlung)

| Command | Use-Case |
|---|---|
| `/confirm` | Entry-Rec übernehmen mit auto-shares + rec-Preis (reply auf Bot-Msg, ohne args) |
| `/confirm 3` | 3 Stück, rec-Preis (reply) |
| `/confirm 3 @172.50` | 3 Stück, eigener Fill-Preis (reply) |
| `/confirm NVD.DE 3 @172.50` | standalone (kein reply nötig) |
| `/cancel` | pending Rec verwerfen (reply oder `/cancel TICKER`) |
| `/close TICKER @PREIS` | Position schließen nach TR-Sell |
| `/close TICKER @PREIS #tag` | mit Mistake-Tag bei Verlust |

**Mistake-Tags:** `thesis_wrong / timing_early / timing_late / whipsaw / slippage / sl_too_tight / news_shock / regime_shift`

### Position-Pflege

| Command | Use-Case |
|---|---|
| `/add TICKER STK X @PREIS` | manuell aufstocken (z.B. `/add RWE.DE STK 2 @61.60`) |
| `/dividend TICKER €BETRAG [grund]` | Dividende verbuchen → Cash + Equity-Curve |

### Watchlevels

| Command | Use-Case |
|---|---|
| `/watch TICKER TYPE @PREIS [thesis...]` | Watchlevel manuell setzen |
| `/watchlist` | aktive Watchlevels anzeigen |
| `/watchremove TICKER` | alle Levels für Ticker raus |
| `/watchclear yes` | Hard-Wipe ALLE Watchlevels (`yes` Pflicht) |

**Watchlevel-Types:**
- `breakout_long` — Ausbruch über Resistance (mit Vol-Confirm)
- `support_bounce` — Bounce an Support (MA50/MA200/Trendline)
- `resistance_reject` — Reject an Widerstand (für Exit-Trigger)
- `inverse_etf_entry` — Bearish Setup via Inverse ETF

Beispiel: `/watch RWE.DE breakout_long @62.50 Goldman 68 target + Analyst-Upgrade`

### System / Safety

| Command | Use-Case |
|---|---|
| `/positions` | Portfolio + offene Trades + pending |
| `/morning` | Morning-Prep manuell re-runnen (Sonnet, ~10s, €0.02) |
| `/panic [grund]` | Kill-Switch AN (keine neuen Entries; SL/TP läuft weiter) |
| `/resume` | Kill-Switch AUS |
| `/killstatus` | Kill-Switch Status |
| `/help` | Help-Text |

---

## Bot sendet

### Action-Telegrams (alle mit `/confirm`-Flow)

| Header | Was tun |
|---|---|
| `🎯 ENTRY \| TICKER \| X Stk = €Y` | TR-Order + reply `/confirm` |
| `🎯 ADD \| TICKER \| +€X` | TR-Add + reply `/confirm` |
| `🔧 UPDATE \| TICKER \| SL→€X TP→€Y` | nur reply `/confirm` (kein TR-Trade) |
| `🎯 EXIT \| TICKER \| 🚨/⚠️/🕐 urgency` | TR-Sell + `/confirm` + dann `/close TICKER @PREIS` |

**EXIT-Urgency:**
- `🚨 now` — sofort schließen (SL-near, news-shock)
- `⚠️ today` — im Lauf des Tages (thesis-degradation)
- `🕐 eod` — bei Schlusskurs (graceful exit)

### Auto-Alerts (info, kein /confirm)

| Emoji | Meaning |
|---|---|
| `🚨 STOP-LOSS HIT` | Position Stop getroffen → schließen JETZT auf TR + `/close` |
| `🎉 TAKE-PROFIT HIT` | TP getroffen, Vollverkauf |
| `🎯 PARTIAL-TP` | TP1 hit, 50% raus, Rest läuft mit BE-SL + Trailing |
| `🛡️ Stop auf Break-Even` | SL auf Entry gezogen nach TP1 |
| `📐 Trailing aktiviert` | 1.5×ATR Trailing-Stop läuft jetzt |
| `📈 Trailing-Stop nachgezogen` | SL hochgezogen (max 1× / 15min) |
| `⚠️ Approaching Stop` | Preis nahe SL (max 1×/Tag) |
| `🌅 Morning OK` | Sonnet gelaufen, X Watchlevels gesetzt |
| `💸 EX-DIV WARNUNG` | Mechanischer Drop droht SL zu triggern |
| `🚨 EARNINGS MORGEN` | T-1 vor Earnings, close empfohlen |
| `🕒 STALE THESIS` | Position > hold_days_max gehalten |
| `📊 EOD <date>` | End-of-Day Summary (22:10) |
| `📅 WEEKEND-RECAP` | Sa/So 10:00 |
| `⛔ ENTRY BLOCKIERT` | nur bei `risk_halt` (Bot-wide safety) |

---

## Wie es zusammenhängt

### Sonnet → Haiku → Trade-Lifecycle

```
08:00 Sonnet morgens
   ├─ scannt Markt
   ├─ setzt 3-7 Watchlevels (set_watch_levels)
   ├─ ggf. ENTRY-Empfehlung (recommend_entry)
   └─ pro offene Position: HOLD / UPDATE-SL-TP / EXIT?

Tag-über (alle 15min):
   ├─ events.py prüft Watchlevels gegen Live-Preise
   ├─ Watch-Hit / News / Price-Alert → Haiku Event-Mode
   └─ Haiku verifiziert Conditions, kein Re-Reasoning
       └─ Conditions OK → recommend_entry / recommend_add / update_targets / recommend_exit

Mechanisch (kein Claude):
   ├─ TP1 hit → 50% raus + BE-SL + 1.5×ATR Trailing aktiv
   ├─ SL hit → Telegram (du closest auf TR)
   ├─ Held > hold_days_max → 🕒 STALE THESIS Alert (kein auto-close)
   └─ invalidate_below durchbrochen → Watchlevel weg + Event
```

### `/confirm` Reply-Pattern

Telegram-Replies arbeiten via `message_id`. Klick auf Bot-Empfehlung → "Reply" → `/confirm` findet richtige pending-Rec automatisch. Ohne reply: `/confirm TICKER ...` als standalone.

### TTL + Slippage

- **TTL:** Pending-Recs verfallen nach 4h (`PENDING_REC_TTL_HOURS`). `/confirm` nach Ablauf → reject ("veraltet").
- **Slippage-Gate:** Wenn `|fill_price − rec_price| / rec_price > MAX_ENTRY_SLIPPAGE_PERCENT` (adaptive, default 2%) → `/confirm` rejected. Re-quoten oder `/cancel`.

### UPDATE-Validation (Schutz vor Sonnet-Bullshit)

- `new_stop_loss` darf nur HÖHER als alter SL (nie loosening). Muss < entry.
- `new_take_profit` muss > entry.
- Beide optional, aber min 1 muss kommen.
- Failed → silent log, kein Telegram, kein pending-rec.

---

## Cost-Profile

| Mode | Modell | Cost/Call | Frequenz |
|---|---|---|---|
| Morning | Sonnet 4.6 | €0.02-0.03 | 1× / Tag |
| Opening (XETRA + US) | Haiku 4.5 | €0.01 each | 2× / Tag |
| Event (Watch-Hit / News / Price) | Haiku 4.5 | €0.01 | 0-5× / Tag |
| Red-Team (wenn entry-rec passt alle Gates) | gleicher Tier | +50% | per entry |

**Daily-Cap:** `MAX_ANALYSES_PER_DAY=20` hard-limit, `MIN_MINUTES_BETWEEN_ANALYSES=45` cooldown. Forced-call (geo-news) cooldown 15min separat.

**Realistische Daily-Cost: €0.05-0.15.**

---

## Cost-Reduction Mechanics (was reduziert Calls)

1. **News pre-gate:** Stock-News auf Ticker ohne `open_trades` UND ohne `watch_levels` → kein Claude-Call.
2. **Liquidity-Gate:** Tickers mit `volume_ratio < 0.3` ODER `spread > 0.75%` werden gedroppt BEVOR Claude sie sieht (open-Position bleibt drin für Exit).
3. **VWAP-Anomaly-Gate:** Watch-Hit mit `|vwap_dev_atr| ≥ 3.0` → Event gedroppt (likely flash-spike).
4. **Watch-Level-Dedup:** Same trigger same day → no repeat call (`triggered_events` cache).
5. **Price-Alert-Dedup:** Same direction + ticker same day → no repeat call.
6. **News-Headline-Dedup:** MD5(title) cache same-day.
7. **Setup-Quality-Gates (silent post-Claude):** edge / sector / RS / volume / confluence / correlation → `gate_blocks.jsonl`, kein Telegram.

---

## Pending-Recs Übersicht

`portfolio.json → pending_recommendations[]` enthält 4 Typen:

| `kind` (oder fehlt = entry) | Origin | /confirm-Effekt |
|---|---|---|
| (none) → ENTRY | `recommend_entry` Tool | neuer open_trade Eintrag (TR-Fill via /confirm) |
| `add` | `recommend_add_to_position` | weighted-avg in existing open_trade |
| `update` | `update_position_targets` | nur SL/TP-Update auf existing open_trade, kein TR-Trade |
| `exit` | `recommend_exit` | rec gelöscht + `/close TICKER @PREIS` Hint reply |

---

## Trade-Lifecycle Beispiel

```
08:00  Bot:  🌅 Morning OK | 5 Watchlevels | 1 Position
       Bot:  🎯 ENTRY | NVD.DE | 1 Stk à €175.20 = €175.20
             Conv 4/5 | SL €172 | TP €180/€185 | Hold 3-7d
             Reply /confirm

08:01  Du:   /confirm  (reply auf Bot-Msg)
       Du:   [auf TR Order ausgeführt]

11:42  Bot:  🎯 PARTIAL-TP | NVD.DE | TP1 €180 hit
             VERKAUFE 0.5 Stk auf TR jetzt
       Du:   [auf TR 0.5 Stk verkauft]

13:15  Bot:  🔧 UPDATE | NVD.DE | SL €175 → €178 TP €185 → €188
             Grund: Goldman raised target to €190, Resistance broke
             Reply /confirm

13:16  Du:   /confirm

14:30  Bot:  🎯 EXIT | NVD.DE | ⚠️ today
             Grund: MACD bearish cross + Vol-Distribution
             Reply /confirm + /close NVD.DE @PREIS

14:31  Du:   /confirm
       Du:   [auf TR verkauft @184]
       Du:   /close NVD.DE @184.00
       Bot:  🎉 NVD.DE geschlossen | P&L +€8.40 (+5.0%)
```
