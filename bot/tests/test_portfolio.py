"""Unit tests for core.portfolio pure-logic functions.

Position sizing, risk gates, confluence, correlation, hit-stats. No disk I/O —
every function under test takes plain dict/list args.
"""

import unittest
from datetime import datetime, timedelta

import config
import core.portfolio as P


def _brier_trades(n, brier, pnl_pct=1.0):
    """n closed trades each carrying a fixed brier score."""
    return [{"brier": brier, "pnl_pct": pnl_pct} for _ in range(n)]


def _slip_trades(n, slip):
    return [{"slippage_pct": slip} for _ in range(n)]


class TestComputeKellyMult(unittest.TestCase):
    def test_below_10_scored_falls_back_to_config(self):
        self.assertEqual(P.compute_kelly_mult(_brier_trades(5, 0.1)), config.KELLY_FRACTION)

    def test_perfect_calibration_gives_max(self):
        self.assertEqual(P.compute_kelly_mult(_brier_trades(10, 0.0)), 0.50)

    def test_random_calibration_gives_floor(self):
        # brier 0.25 == coin-flip → 0.50 - 1.0*0.40 = 0.10
        self.assertEqual(P.compute_kelly_mult(_brier_trades(10, 0.25)), 0.10)

    def test_mid_calibration_linear(self):
        # brier 0.125 → 0.50 - 0.5*0.40 = 0.30
        self.assertEqual(P.compute_kelly_mult(_brier_trades(12, 0.125)), 0.30)

    def test_clamped_to_floor_for_terrible_brier(self):
        self.assertEqual(P.compute_kelly_mult(_brier_trades(10, 0.5)), 0.10)

    def test_only_numeric_brier_counts_as_scored(self):
        trades = _brier_trades(9, 0.0) + [{"brier": None}, {"brier": "bad"}]
        # 9 scored < 10 → fallback despite 11 trades total
        self.assertEqual(P.compute_kelly_mult(trades), config.KELLY_FRACTION)


class TestComputeSlippageBudget(unittest.TestCase):
    def test_below_10_returns_base(self):
        self.assertEqual(
            P.compute_slippage_budget(_slip_trades(5, 0.5)),
            config.MAX_ENTRY_SLIPPAGE_PERCENT,
        )

    def test_low_avg_keeps_base(self):
        self.assertEqual(
            P.compute_slippage_budget(_slip_trades(10, 0.2)),
            config.MAX_ENTRY_SLIPPAGE_PERCENT,
        )

    def test_hot_avg_clamps_to_one(self):
        self.assertEqual(P.compute_slippage_budget(_slip_trades(10, 1.5)), 1.0)

    def test_mid_avg_linear(self):
        # avg 0.65 → base - (0.65-0.3)/0.7 * (base-1.0)
        base = config.MAX_ENTRY_SLIPPAGE_PERCENT
        expected = round(base - (0.65 - 0.3) / 0.7 * (base - 1.0), 2)
        self.assertEqual(P.compute_slippage_budget(_slip_trades(10, 0.65)), expected)


class TestSuggestPositionSize(unittest.TestCase):
    CAP = 1000.0

    def test_no_atr_uses_10pct_fallback(self):
        self.assertEqual(P.suggest_position_size(None, self.CAP), 100.0)

    def test_hard_cap_enforced(self):
        # tiny ATR → huge ATR-leg → must clip to MAX_POSITION_SIZE_PERCENT
        size = P.suggest_position_size(0.5, self.CAP, risk_pct=3.0)
        self.assertEqual(size, self.CAP * config.MAX_POSITION_SIZE_PERCENT / 100)

    def test_negative_kelly_edge_returns_zero(self):
        size = P.suggest_position_size(2.0, self.CAP, p_win=0.3, reward_to_risk=1.0)
        self.assertEqual(size, 0.0)

    def test_kelly_leg_caps_atr_leg(self):
        # f* = (0.6*2 - 0.4)/2 = 0.4 ; kelly_eur = 1000*0.4*0.25 = 100
        size = P.suggest_position_size(
            0.5, self.CAP, p_win=0.6, reward_to_risk=2.0, kelly_mult=0.25
        )
        self.assertEqual(size, 100.0)

    def test_kelly_mult_defaults_to_config(self):
        explicit = P.suggest_position_size(
            0.5, self.CAP, p_win=0.6, reward_to_risk=2.0, kelly_mult=config.KELLY_FRACTION
        )
        implicit = P.suggest_position_size(0.5, self.CAP, p_win=0.6, reward_to_risk=2.0)
        self.assertEqual(explicit, implicit)

    def test_never_exceeds_hard_cap(self):
        for atr in (None, 0.3, 1.0, 5.0):
            size = P.suggest_position_size(atr, self.CAP)
            self.assertLessEqual(size, self.CAP * config.MAX_POSITION_SIZE_PERCENT / 100)


class TestMaxAffordableSharePrice(unittest.TestCase):
    def test_equals_config_cap_times_buffer(self):
        self.assertEqual(
            P.max_affordable_share_price_eur({}),
            config.MAX_SHARE_PRICE_EUR * config.WHOLE_SHARE_PRICE_BUFFER,
        )


class TestEdgeOk(unittest.TestCase):
    def test_none_p_win_rejected(self):
        self.assertEqual(P.edge_ok(None, 100, 95, 110), (False, 0.0))

    def test_p_win_out_of_range_rejected(self):
        self.assertEqual(P.edge_ok(1.5, 100, 95, 110), (False, 0.0))
        self.assertEqual(P.edge_ok(0.0, 100, 95, 110), (False, 0.0))

    def test_stop_above_entry_rejected(self):
        self.assertEqual(P.edge_ok(0.6, 100, 105, 110), (False, 0.0))

    def test_tp_below_entry_rejected(self):
        self.assertEqual(P.edge_ok(0.6, 100, 95, 99), (False, 0.0))

    def test_positive_edge_passes(self):
        # b = (115-100)/(100-95) = 3.0 ; edge = 0.6*3 - 0.4 = 1.4
        ok, edge = P.edge_ok(0.6, 100, 95, 115)
        self.assertTrue(ok)
        self.assertAlmostEqual(edge, 1.4, places=6)

    def test_list_take_profit_uses_first(self):
        ok_list, edge_list = P.edge_ok(0.6, 100, 95, [115, 130])
        ok_scalar, edge_scalar = P.edge_ok(0.6, 100, 95, 115)
        self.assertEqual((ok_list, edge_list), (ok_scalar, edge_scalar))

    def test_thin_edge_blocked_by_threshold(self):
        ok, edge = P.edge_ok(0.4, 100, 95, 102)
        self.assertFalse(ok)
        self.assertLess(edge, config.MIN_EXPECTED_EDGE)


class TestComputeConfluence(unittest.TestCase):
    FULL_SNAP = {
        "price": 110, "ma20": 100, "ma50": 95, "rsi14": 55,
        "macd": 1.0, "macd_signal": 0.5, "volume_ratio": 1.2,
        "spread_pct": 0.2, "wk_trend": "UP", "rs_20d_vs_index_pct": 3.0,
        "analyst_rec_key": "buy", "analyst_upside_pct": 10,
    }

    def test_no_data_scores_zero(self):
        for bad in ({}, {"error": "x"}, {"price": None}):
            res = P.compute_confluence(bad, "RISK_ON")
            self.assertEqual(res["score"], 0)
            self.assertIn("no_data", res["missing"])

    def test_full_bullish_snap_scores_ten(self):
        res = P.compute_confluence(self.FULL_SNAP, "RISK_ON")
        self.assertEqual(res["score"], 10)
        self.assertEqual(res["missing"], [])

    def test_single_missing_item_drops_score(self):
        snap = dict(self.FULL_SNAP, wk_trend="DOWN")
        res = P.compute_confluence(snap, "RISK_ON")
        self.assertEqual(res["score"], 9)
        self.assertIn("wk_trend_up", res["missing"])

    def test_risk_off_regime_loses_point(self):
        res = P.compute_confluence(self.FULL_SNAP, "RISK_OFF")
        self.assertFalse(res["items"]["regime_risk_on"])


class TestComputeBaseQuality(unittest.TestCase):
    # Perfect base snapshot — should hit every positive item (max score 10).
    PERFECT = {
        "price": 50.0,
        "volume_ratio": 0.5,           # selling_exhaustion (+2)
        "range_compression": 0.4,      # atr_contraction (+2)
        "intraday_low": 48.5,          # below ma50
        "ma50": 49.5,                  # < price → failed_breakdown_reclaim (+2)
        "higher_lows_5d": 4,           # higher_lows (+1) + strong_higher_lows (+1)
        "day_high": 50.4,
        "day_low": 49.8,               # range 0.6 / 50 = 1.2% < 0.7 × 2% = 1.4% → tight (+1)
        "atr14_pct": 2.0,
        "pct_below_52w_high": -15,     # in_base_zone (+1)
    }

    def test_no_data_returns_zero(self):
        for bad in ({}, {"error": "x"}, {"price": None}):
            res = P.compute_base_quality(bad)
            self.assertEqual(res["score"], 0)
            self.assertIn("no_data", res["missing"])

    def test_perfect_snap_scores_ten(self):
        res = P.compute_base_quality(self.PERFECT)
        self.assertEqual(res["score"], 10)

    def test_selling_exhaustion_weighted_two(self):
        bare = {"price": 50.0, "volume_ratio": 0.5}
        self.assertEqual(P.compute_base_quality(bare)["items"]["selling_exhaustion"], 2)
        full_vol = {"price": 50.0, "volume_ratio": 1.2}
        self.assertEqual(P.compute_base_quality(full_vol)["items"]["selling_exhaustion"], 0)

    def test_failed_breakdown_reclaim_requires_low_below_ma50_and_price_above(self):
        good = {"price": 50.0, "intraday_low": 48.0, "ma50": 49.0}
        self.assertEqual(P.compute_base_quality(good)["items"]["failed_breakdown_reclaim"], 2)
        # price still under ma50 → no reclaim
        bad = {"price": 48.5, "intraday_low": 48.0, "ma50": 49.0}
        self.assertEqual(P.compute_base_quality(bad)["items"]["failed_breakdown_reclaim"], 0)

    def test_higher_lows_thresholds(self):
        self.assertEqual(P.compute_base_quality({"price": 50, "higher_lows_5d": 1})["items"]["higher_lows"], 0)
        self.assertEqual(P.compute_base_quality({"price": 50, "higher_lows_5d": 2})["items"]["higher_lows"], 1)
        self.assertEqual(P.compute_base_quality({"price": 50, "higher_lows_5d": 4})["items"]["strong_higher_lows"], 1)
        self.assertEqual(P.compute_base_quality({"price": 50, "higher_lows_5d": 3})["items"]["strong_higher_lows"], 0)

    def test_base_zone_window(self):
        self.assertEqual(P.compute_base_quality({"price": 50, "pct_below_52w_high": -15})["items"]["in_base_zone"], 1)
        # Outside [-25, -8] both ends
        self.assertEqual(P.compute_base_quality({"price": 50, "pct_below_52w_high": -5})["items"]["in_base_zone"], 0)
        self.assertEqual(P.compute_base_quality({"price": 50, "pct_below_52w_high": -30})["items"]["in_base_zone"], 0)

    def test_score_capped_at_ten(self):
        # Even if all bonuses fire, score never exceeds 10
        res = P.compute_base_quality(self.PERFECT)
        self.assertLessEqual(res["score"], 10)


class TestComputeCorrelations(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.pd = pd
        rising = pd.Series(range(25), dtype=float)
        self.returns = {
            "A": rising,
            "B": rising.copy(),                       # corr +1 with A
            "C": pd.Series(range(25, 0, -1), dtype=float),  # corr -1 with A
            "SHORT": pd.Series(range(5), dtype=float),      # too short
        }

    def test_missing_candidate_returns_empty(self):
        self.assertEqual(P.compute_correlations(self.returns, "NOPE"), {})

    def test_short_candidate_series_returns_empty(self):
        self.assertEqual(P.compute_correlations(self.returns, "SHORT"), {})

    def test_perfect_and_inverse_correlation(self):
        corr = P.compute_correlations(self.returns, "A")
        self.assertEqual(corr["B"], 1.0)
        self.assertEqual(corr["C"], -1.0)

    def test_short_other_series_omitted(self):
        corr = P.compute_correlations(self.returns, "A")
        self.assertNotIn("SHORT", corr)
        self.assertNotIn("A", corr)  # candidate never correlated with itself


class TestTradeDividends(unittest.TestCase):
    TRADE = {
        "ticker": "RWE", "entry_date": "2026-05-01 10:00",
        "exit_date": "2026-05-10 15:00", "pnl_eur": -13.5, "pnl_pct": -5.0,
        "size_eur": 270.0,
    }
    DIV = {
        "kind": "dividend", "amount": 7.2,
        "linked_trade": {
            "ticker": "RWE", "entry_date": "2026-05-01 10:00",
            "exit_date": "2026-05-10 15:00",
        },
    }

    def test_matching_dividend_linked(self):
        self.assertEqual(P.trade_dividends(self.TRADE, [self.DIV]), [self.DIV])

    def test_ticker_mismatch_not_linked(self):
        other = dict(self.DIV, linked_trade=dict(self.DIV["linked_trade"], ticker="BAS"))
        self.assertEqual(P.trade_dividends(self.TRADE, [other]), [])

    def test_effective_pnl_folds_dividend(self):
        self.assertEqual(P.effective_pnl_eur(self.TRADE, [self.DIV]), -6.3)

    def test_effective_pnl_pct_folds_dividend(self):
        # base -5.0 + (7.2 / 270 * 100) = -5.0 + 2.67
        self.assertEqual(P.effective_pnl_pct(self.TRADE, [self.DIV]), -2.33)

    def test_no_movements_returns_base(self):
        self.assertEqual(P.effective_pnl_eur(self.TRADE, []), -13.5)


class TestComputeHitStats(unittest.TestCase):
    def test_under_three_trades_returns_none(self):
        self.assertIsNone(P.compute_hit_stats([{"pnl_pct": 1}, {"pnl_pct": 2}]))

    def test_basic_win_rate_and_r_multiple(self):
        trades = [{"pnl_pct": 5}, {"pnl_pct": 3}, {"pnl_pct": -2}]
        stats = P.compute_hit_stats(trades)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["win_rate"], 66.7)
        self.assertEqual(stats["avg_win_pct"], 4.0)
        self.assertEqual(stats["avg_loss_pct"], -2.0)
        self.assertEqual(stats["r_multiple"], 2.0)

    def test_exact_zero_pnl_excluded_from_buckets(self):
        trades = [{"pnl_pct": 5}, {"pnl_pct": -3}, {"pnl_pct": 0}, {"pnl_pct": 4}]
        stats = P.compute_hit_stats(trades)
        # win_rate is wins/total; zero counts in total but not as a win
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["win_rate"], 50.0)

    def test_calibration_haircut_active_at_min_n(self):
        # 10 trades, p_win 0.6, all won → bias = 0.6 - 1.0 = -0.4
        trades = [
            {"pnl_pct": 4, "p_win": 0.6, "brier": 0.16, "outcome": 1}
            for _ in range(10)
        ]
        stats = P.compute_hit_stats(trades)
        cal = stats["calibration"]
        self.assertEqual(cal["n"], 10)
        self.assertEqual(cal["haircut"], -0.4)

    def test_calibration_no_haircut_below_min_n(self):
        trades = [
            {"pnl_pct": 4, "p_win": 0.6, "brier": 0.16, "outcome": 1}
            for _ in range(config.MIN_CALIBRATION_N - 1)
        ]
        stats = P.compute_hit_stats(trades)
        self.assertEqual(stats["calibration"]["haircut"], 0.0)

    def test_by_base_quality_buckets_correctly(self):
        # Mix of trades across the three BQ buckets, with explicit outcomes
        trades = [
            {"pnl_pct": 5, "base_quality_at_entry": 8},   # strong, win
            {"pnl_pct": -2, "base_quality_at_entry": 7},  # strong, loss
            {"pnl_pct": 3, "base_quality_at_entry": 5},   # building, win
            {"pnl_pct": -4, "base_quality_at_entry": 4},  # building, loss
            {"pnl_pct": -3, "base_quality_at_entry": 2},  # no_base, loss
        ]
        stats = P.compute_hit_stats(trades)
        bbq = stats["by_base_quality"]
        self.assertEqual(bbq["strong"]["total"], 2)
        self.assertEqual(bbq["strong"]["wins"], 1)
        self.assertEqual(bbq["strong"]["rate"], 50.0)
        self.assertEqual(bbq["building"]["total"], 2)
        self.assertEqual(bbq["building"]["wins"], 1)
        self.assertEqual(bbq["no_base"]["total"], 1)
        self.assertEqual(bbq["no_base"]["wins"], 0)
        self.assertEqual(bbq["no_base"]["avg_pnl_pct"], -3.0)

    def test_by_base_quality_skips_trades_without_field(self):
        # Legacy closed_trades (no `base_quality_at_entry`) must not appear
        # in the bucket — the metric only accumulates for instrumented trades
        # going forward (no retroactive data).
        trades = [
            {"pnl_pct": 4},                              # legacy, skipped
            {"pnl_pct": -1},                             # legacy, skipped
            {"pnl_pct": 5, "base_quality_at_entry": 8},  # new, bucketed
        ]
        stats = P.compute_hit_stats(trades)
        self.assertEqual(stats["by_base_quality"]["strong"]["total"], 1)
        # Total trades count stays 3 (overall stats unaffected)
        self.assertEqual(stats["total"], 3)

    def test_by_base_quality_threshold_boundaries(self):
        # Boundary: 7 → strong, 6 → building; 4 → building, 3 → no_base.
        trades = [
            {"pnl_pct": 1, "base_quality_at_entry": 7},  # strong
            {"pnl_pct": 1, "base_quality_at_entry": 6},  # building
            {"pnl_pct": 1, "base_quality_at_entry": 4},  # building
            {"pnl_pct": 1, "base_quality_at_entry": 3},  # no_base
        ]
        stats = P.compute_hit_stats(trades)
        self.assertEqual(stats["by_base_quality"]["strong"]["total"], 1)
        self.assertEqual(stats["by_base_quality"]["building"]["total"], 2)
        self.assertEqual(stats["by_base_quality"]["no_base"]["total"], 1)


class TestRiskHaltStatus(unittest.TestCase):
    def _pf(self, **kw):
        base = {"open_trades": [], "closed_trades": [], "total_capital_eur": 1000.0}
        base.update(kw)
        return base

    def test_clean_portfolio_no_halt(self):
        res = P.risk_halt_status(self._pf())
        self.assertFalse(res["halt"])
        self.assertEqual(res["reasons"], [])

    def test_kill_switch_halts(self):
        res = P.risk_halt_status(self._pf(kill_switch=True, kill_switch_reason="panic"))
        self.assertTrue(res["halt"])
        self.assertTrue(any("KILL-SWITCH" in r for r in res["reasons"]))

    def test_daily_loss_cap_halts(self):
        today = datetime.now().strftime("%Y-%m-%d")
        # -6% of 1000 capital ≤ -DAILY_LOSS_HALT_PERCENT(5%)
        pf = self._pf(closed_trades=[{"exit_date": f"{today} 14:00", "pnl_eur": -60.0}])
        res = P.risk_halt_status(pf)
        self.assertTrue(res["halt"])
        self.assertTrue(any("Daily-Loss" in r for r in res["reasons"]))

    def test_portfolio_heat_halts(self):
        # one position risking 10% of capital → heat ≥ MAX_PORTFOLIO_HEAT_PERCENT
        pf = self._pf(open_trades=[{
            "ticker": "X", "entry_price": 100, "stop_loss": 80, "shares": 5,
            "entry_date": "2020-01-01 10:00",
        }])
        res = P.risk_halt_status(pf)
        self.assertTrue(res["halt"])
        self.assertTrue(any("Heat" in r for r in res["reasons"]))

    def test_max_active_trades_halts(self):
        small = {
            "ticker": "X", "entry_price": 10, "stop_loss": 9, "shares": 1,
            "entry_date": "2020-01-01 10:00",
        }
        pf = self._pf(open_trades=[dict(small) for _ in range(config.MAX_ACTIVE_TRADES)])
        res = P.risk_halt_status(pf)
        self.assertTrue(res["halt"])
        self.assertTrue(any("Max-Active-Trades" in r for r in res["reasons"]))


class TestDrawdownState(unittest.TestCase):
    def test_dd_scaling_halves_in_soft_band(self):
        # 5% drawdown sits between SOFT(4%) and HALT(8%)
        pf = {"total_capital_eur": 1000.0,
              "closed_trades": [{"exit_date": "2026-01-01", "pnl_eur": -50.0}]}
        self.assertEqual(P.dd_scaling_factor(pf), 0.5)

    def test_dd_scaling_full_below_soft(self):
        pf = {"total_capital_eur": 1000.0,
              "closed_trades": [{"exit_date": "2026-01-01", "pnl_eur": -20.0}]}
        self.assertEqual(P.dd_scaling_factor(pf), 1.0)

    def test_dd_scaling_full_above_hard_halt(self):
        # past HALT the soft modifier no longer applies (hard halt owns it)
        pf = {"total_capital_eur": 1000.0,
              "closed_trades": [{"exit_date": "2026-01-01", "pnl_eur": -100.0}]}
        self.assertEqual(P.dd_scaling_factor(pf), 1.0)

    def test_maintain_drawdown_latches_halt(self):
        pf = {"total_capital_eur": 1000.0,
              "closed_trades": [{"exit_date": "2026-01-01", "pnl_eur": -100.0}]}
        changed = P.maintain_drawdown_state(pf)
        self.assertTrue(changed)
        self.assertTrue(pf["dd_halt_active"])


class TestPortfolioHeat(unittest.TestCase):
    def test_normal_position_risk(self):
        pf = {"total_capital_eur": 1000.0, "open_trades": [
            {"ticker": "X", "entry_price": 100, "stop_loss": 90, "shares": 2},
        ]}
        heat = P.compute_portfolio_heat(pf)
        self.assertEqual(heat["total_heat_eur"], 20.0)  # (100-90)*2
        self.assertEqual(heat["warnings"], [])

    def test_missing_stop_uses_full_position_value(self):
        pf = {"total_capital_eur": 1000.0, "open_trades": [
            {"ticker": "X", "entry_price": 100, "stop_loss": 0, "shares": 2},
        ]}
        heat = P.compute_portfolio_heat(pf)
        self.assertEqual(heat["total_heat_eur"], 200.0)
        self.assertTrue(heat["warnings"])

    def test_stop_above_entry_flagged_zero_risk(self):
        pf = {"total_capital_eur": 1000.0, "open_trades": [
            {"ticker": "X", "entry_price": 100, "stop_loss": 110, "shares": 2},
        ]}
        heat = P.compute_portfolio_heat(pf)
        self.assertEqual(heat["total_heat_eur"], 0.0)
        self.assertTrue(any("SL ≥ Entry" in w for w in heat["warnings"]))


class TestSectorExposure(unittest.TestCase):
    def test_unknown_ticker_goes_to_other(self):
        pf = {"open_trades": [{"ticker": "ZZZZ_NOT_REAL"}]}
        by_sector = P.compute_sector_exposure(pf)
        self.assertEqual(by_sector, {"other": ["ZZZZ_NOT_REAL"]})

    def test_known_ticker_grouped_by_sector_map(self):
        known = next(iter(config.SECTOR_MAP))
        pf = {"open_trades": [{"ticker": known}]}
        by_sector = P.compute_sector_exposure(pf)
        self.assertIn(config.SECTOR_MAP[known], by_sector)
        self.assertIn(known, by_sector[config.SECTOR_MAP[known]])


class TestExitSuppressedTickers(unittest.TestCase):
    def test_pending_exit_rec_suppresses(self):
        pf = {"pending_recommendations": [{"kind": "exit", "ticker": "rwe"}],
              "open_trades": []}
        self.assertEqual(P.exit_suppressed_tickers(pf), {"RWE"})

    def test_recent_auto_drop_suppresses(self):
        recent = datetime.now().strftime("%Y-%m-%d %H:%M")
        pf = {"pending_recommendations": [],
              "open_trades": [{"ticker": "bas", "exit_dropped_at": recent}]}
        self.assertEqual(P.exit_suppressed_tickers(pf), {"BAS"})

    def test_old_auto_drop_not_suppressed(self):
        old = (datetime.now() - timedelta(
            minutes=config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP + 60
        )).strftime("%Y-%m-%d %H:%M")
        pf = {"pending_recommendations": [],
              "open_trades": [{"ticker": "bas", "exit_dropped_at": old}]}
        self.assertEqual(P.exit_suppressed_tickers(pf), set())


if __name__ == "__main__":
    unittest.main()
