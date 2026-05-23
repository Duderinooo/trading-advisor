"""Tests for DecisionResult + GateDecision + run_entry_gates trace."""

import unittest
from unittest.mock import patch

from core.llm.handlers.gates.context import (
    DecisionResult, GateContext, GateDecision,
)


def _ctx(ticker="BAS.DE", mode="morning"):
    """Minimal stub GateContext for unit-tests (skips build_gate_context which
    calls load_portfolio + compute_hit_stats). Mode=morning by default to
    bypass event-watch-coupling check."""
    return GateContext(
        mode=mode,
        market_data={ticker: {"price": 53.0, "atr14": 1.0, "atr14_pct": 1.9}},
        market_ctx={"^VIX": {"price": 15.0}},
        regime="RISK_ON",
        cash=900.0,
        model="claude-sonnet-4-6",
        portfolio={"open_trades": [], "watch_levels": [], "closed_trades": []},
        stats=None,
        ticker=ticker,
        snap_md={"price": 53.0, "atr14": 1.0, "atr14_pct": 1.9},
    )


class TestGateDecision(unittest.TestCase):
    def test_frozen(self):
        d = GateDecision(gate="gate_edge", passed=False, elapsed_ms=1.2)
        with self.assertRaises(Exception):
            d.passed = True  # type: ignore[misc]


class TestDecisionResultToTree(unittest.TestCase):
    def test_passed_emits_pass_label(self):
        result = DecisionResult(
            ticker="BAS.DE", passed=True, blocked_by=None,
            decisions=[
                GateDecision("gate_required_fields", True, 0.5),
                GateDecision("gate_edge", True, 1.8),
            ],
            final_rec={"ticker": "BAS.DE"},
            timestamp="2026-05-23 14:32:00",
        )
        tree = result.to_tree()
        self.assertEqual(tree["decision"], "PASS")
        self.assertIsNone(tree["blocked_by"])
        self.assertEqual(len(tree["path"]), 2)
        self.assertIn("gate_edge -> PASS", tree["path"][1])

    def test_blocked_emits_block_label(self):
        result = DecisionResult(
            ticker="BAS.DE", passed=False, blocked_by="gate_edge",
            decisions=[
                GateDecision("gate_required_fields", True, 0.4),
                GateDecision("gate_edge", False, 2.1),
            ],
            final_rec=None,
            timestamp="2026-05-23 14:32:00",
        )
        tree = result.to_tree()
        self.assertEqual(tree["decision"], "BLOCKED")
        self.assertEqual(tree["blocked_by"], "gate_edge")
        self.assertIn("gate_edge -> FAIL", tree["path"][1])


class TestRunEntryGatesShortCircuits(unittest.TestCase):
    """Verify run_entry_gates returns immediately on first block (no later gates
    run) + populates DecisionResult chain up to and including the blocker."""

    def test_required_fields_block_short_circuits(self):
        from core.llm.handlers.gates.context import run_entry_gates
        # Missing required fields → gate_required_fields fails first.
        entry = {"ticker": "BAS.DE"}  # no entry_price, sl, tp, etc.
        result = run_entry_gates(entry, _ctx())

        self.assertFalse(result.passed)
        self.assertEqual(result.blocked_by, "gate_required_fields")
        self.assertIsNone(result.final_rec)
        # Only the first gate ran.
        self.assertEqual(len(result.decisions), 1)
        self.assertEqual(result.decisions[0].gate, "gate_required_fields")
        self.assertFalse(result.decisions[0].passed)
        self.assertGreaterEqual(result.decisions[0].elapsed_ms, 0.0)

    def test_decisions_include_passes_before_block(self):
        from core.llm.handlers.gates.context import run_entry_gates
        # Rec missing earnings/setup info but passing required_fields. The
        # already-open gate will fail (no portfolio holding) so we use a valid
        # entry that gets blocked LATER. Easiest: trigger no-entry-zone block
        # by mocking datetime.now().
        entry = {
            "ticker": "BAS.DE",
            "entry_price": 53.0,
            "stop_loss": 52.0,
            "take_profit": [55.0, 57.0],
            "size_eur": 200.0,
            "conviction": 4,
            "p_win": 0.6,
            "thesis": "test thesis",
            "setup_type": "breakout_resistance",
            "top_fail_mode": "reversal",
        }
        # Mock no_entry_window to be active.
        with patch(
            "core.llm.handlers.gates.market.datetime"
        ) as mock_dt:
            # Force time into XETRA open auction window (09:00-09:10).
            from datetime import datetime as real_datetime
            mock_dt.now.return_value = real_datetime(2026, 5, 23, 9, 5, 0)
            result = run_entry_gates(entry, _ctx())

        self.assertFalse(result.passed)
        self.assertEqual(result.blocked_by, "gate_no_entry_zone")
        # At least the 6 earlier gates passed before the block.
        passed_gates = [d.gate for d in result.decisions if d.passed]
        self.assertIn("gate_required_fields", passed_gates)
        self.assertIn("gate_already_open", passed_gates)


class TestPersistDecisionFailSoft(unittest.TestCase):
    """_persist_decision must not raise — analytics dir creation / IO errors
    are swallowed."""

    def test_no_raise_on_io_error(self):
        from core.llm.handlers.recs.entry import _persist_decision
        result = DecisionResult(
            ticker="BAS.DE", passed=True, blocked_by=None,
            decisions=[], final_rec=None, timestamp="2026-01-01 00:00:00",
        )
        # Patch Path so write throws.
        with patch("builtins.open", side_effect=OSError("disk full")):
            try:
                _persist_decision(result)
            except Exception as e:
                self.fail(f"_persist_decision raised: {e}")


if __name__ == "__main__":
    unittest.main()
