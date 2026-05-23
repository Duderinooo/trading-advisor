"""Tests for core/replay/replay_day.py — single-day SL/TP simulation."""

import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import patch


def _bar(date_iso, high, low, close=None):
    return {
        "date": date_iso,
        "open": (high + low) / 2,
        "high": high,
        "low": low,
        "close": close if close is not None else (high + low) / 2,
        "volume": 1_000_000,
    }


class TestReplaySltpStep(unittest.TestCase):
    def _portfolio(self, *trades) -> dict:
        return {"open_trades": list(trades), "cash_eur": 500.0}

    def test_no_open_trades_returns_unchanged(self):
        from core.replay.replay_day import replay_sltp_step
        pf = {"open_trades": []}
        out_pf, result = replay_sltp_step(pf, date(2026, 5, 23))
        self.assertEqual(result.starting_open_count, 0)
        self.assertEqual(result.realized_pnl_eur, 0.0)
        self.assertEqual(result.events, [])

    def test_sl_hit_closes_trade(self):
        from core.replay.replay_day import replay_sltp_step
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
                 "stop_loss": 52.0, "take_profit": [55.0]}
        pf = self._portfolio(trade)
        with patch(
            "core.replay.replay_day.get_daily_bar",
            return_value=_bar("2026-05-23", 53.5, 51.5),  # low 51.5 ≤ SL 52.0
        ):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 23))
        self.assertEqual(result.closed_full, 1)
        self.assertEqual(result.still_open, 0)
        self.assertEqual(out_pf["open_trades"], [])
        self.assertEqual(result.events[0]["type"], "STOP_LOSS_HIT")
        self.assertAlmostEqual(result.realized_pnl_eur, (52.0 - 53.0) * 10, places=2)

    def test_full_tp_hit_closes_trade(self):
        from core.replay.replay_day import replay_sltp_step
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
                 "stop_loss": 52.0, "take_profit": 55.0}  # scalar tp (no partial)
        pf = self._portfolio(trade)
        with patch(
            "core.replay.replay_day.get_daily_bar",
            return_value=_bar("2026-05-23", 55.5, 52.5),  # high 55.5 ≥ TP 55
        ):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 23))
        self.assertEqual(result.closed_full, 1)
        self.assertEqual(result.still_open, 0)
        self.assertEqual(result.events[0]["type"], "TAKE_PROFIT_HIT")
        self.assertAlmostEqual(result.realized_pnl_eur, (55.0 - 53.0) * 10, places=2)

    def test_partial_tp_hit_keeps_remainder_open(self):
        from core.replay.replay_day import replay_sltp_step
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10.0,
                 "stop_loss": 52.0, "take_profit": [55.0, 57.0]}
        pf = self._portfolio(trade)
        with patch(
            "core.replay.replay_day.get_daily_bar",
            return_value=_bar("2026-05-23", 55.5, 52.5),
        ):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 23))
        self.assertEqual(result.closed_partial, 1)
        self.assertEqual(result.still_open, 1)
        # Remainder still open at half-size (50% partial)
        surviving = out_pf["open_trades"][0]
        self.assertEqual(surviving["shares"], 5.0)
        self.assertEqual(surviving["take_profit"], [57.0])  # TP1 popped
        # BE-shift applied (≥ entry)
        self.assertGreaterEqual(surviving["stop_loss"], 53.0)

    def test_ambiguous_keeps_trade_open(self):
        from core.replay.replay_day import replay_sltp_step
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
                 "stop_loss": 52.0, "take_profit": [55.0]}
        pf = self._portfolio(trade)
        with patch(
            "core.replay.replay_day.get_daily_bar",
            return_value=_bar("2026-05-23", 55.5, 51.5),  # both touched
        ):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 23))
        self.assertEqual(result.ambiguous, 1)
        self.assertEqual(result.still_open, 1)
        self.assertEqual(result.realized_pnl_eur, 0.0)
        self.assertEqual(result.events[0]["type"], "AMBIGUOUS")

    def test_missing_bar_leaves_trade_open(self):
        from core.replay.replay_day import replay_sltp_step
        trade = {"ticker": "ZZZ.DE", "entry_price": 100.0, "shares": 5,
                 "stop_loss": 95.0, "take_profit": [110.0]}
        pf = self._portfolio(trade)
        with patch("core.replay.replay_day.get_daily_bar", return_value=None):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 23))
        self.assertEqual(result.bars_missing, ["ZZZ.DE"])
        self.assertEqual(result.still_open, 1)
        self.assertEqual(result.bars_fetched, 0)

    def test_no_touch_stays_open(self):
        from core.replay.replay_day import replay_sltp_step
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
                 "stop_loss": 52.0, "take_profit": [55.0]}
        pf = self._portfolio(trade)
        with patch(
            "core.replay.replay_day.get_daily_bar",
            return_value=_bar("2026-05-23", 53.5, 52.5),
        ):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 23))
        self.assertEqual(result.still_open, 1)
        self.assertEqual(result.closed_full, 0)
        self.assertEqual(result.realized_pnl_eur, 0.0)

    def test_original_portfolio_not_mutated(self):
        from core.replay.replay_day import replay_sltp_step
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
                 "stop_loss": 52.0, "take_profit": [55.0]}
        pf = self._portfolio(trade)
        original = deepcopy(pf)
        with patch(
            "core.replay.replay_day.get_daily_bar",
            return_value=_bar("2026-05-23", 53.5, 51.5),
        ):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 23))
        self.assertEqual(pf, original)  # original untouched
        self.assertNotEqual(out_pf["open_trades"], pf["open_trades"])

    def test_multiple_trades_independent(self):
        from core.replay.replay_day import replay_sltp_step
        t1 = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
              "stop_loss": 52.0, "take_profit": [55.0]}
        t2 = {"ticker": "DBK.DE", "entry_price": 26.0, "shares": 20,
              "stop_loss": 25.0, "take_profit": 28.0}
        pf = self._portfolio(t1, t2)
        bars_by_ticker = {
            "BAS.DE": _bar("2026-05-23", 53.5, 51.5),  # SL hit
            "DBK.DE": _bar("2026-05-23", 28.5, 26.5),  # TP hit
        }
        def _stub(ticker, target_date, **kwargs):
            return bars_by_ticker.get(ticker)
        with patch("core.replay.replay_day.get_daily_bar", side_effect=_stub):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 23))
        self.assertEqual(result.closed_full, 2)
        # BAS.DE SL loss + DBK.DE TP win
        expected_pnl = (52.0 - 53.0) * 10 + (28.0 - 26.0) * 20
        self.assertAlmostEqual(result.realized_pnl_eur, expected_pnl, places=2)


if __name__ == "__main__":
    unittest.main()
