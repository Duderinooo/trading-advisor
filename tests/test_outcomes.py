"""Tests for core/llm/telemetry/outcomes.py — blocked-trade counterfactual.

Network-dependent paths (yfinance fetch in _resolve_against_daily_bar) are not
covered; tests focus on record-skip rules + false-negative-rate aggregation
against synthetic resolved-records.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestRecordSkip(unittest.TestCase):
    def setUp(self):
        # Redirect analytics dir to a tmp folder per test.
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._patch_pending = patch(
            "core.llm.telemetry.outcomes._PENDING_PATH",
            self._tmp_path / "pending.jsonl",
        )
        self._patch_resolved = patch(
            "core.llm.telemetry.outcomes._RESOLVED_PATH",
            self._tmp_path / "resolved.jsonl",
        )
        self._patch_dir = patch(
            "core.llm.telemetry.outcomes._ANALYTICS_DIR", self._tmp_path,
        )
        self._patch_pending.start()
        self._patch_resolved.start()
        self._patch_dir.start()

    def tearDown(self):
        self._patch_pending.stop()
        self._patch_resolved.stop()
        self._patch_dir.stop()
        self._tmp.cleanup()

    def _pending(self) -> list[dict]:
        path = self._tmp_path / "pending.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_skip_gates_dont_record(self):
        from core.llm.telemetry.outcomes import record_blocked_entry
        for gate in (
            "gate_required_fields", "gate_already_open", "gate_event_watch_coupling",
        ):
            record_blocked_entry(
                gate_name=gate, ticker="BAS.DE",
                entry_price=53.0, stop_loss=52.0, take_profit=[55.0],
                setup_type="breakout_resistance", regime="RISK_ON",
            )
        self.assertEqual(len(self._pending()), 0)

    def test_invalid_sl_doesnt_record(self):
        from core.llm.telemetry.outcomes import record_blocked_entry
        # SL >= entry (LONG-invalid)
        record_blocked_entry(
            gate_name="gate_edge", ticker="BAS.DE",
            entry_price=53.0, stop_loss=53.0, take_profit=[55.0],
            setup_type="breakout_resistance", regime="RISK_ON",
        )
        self.assertEqual(len(self._pending()), 0)

    def test_missing_tp_doesnt_record(self):
        from core.llm.telemetry.outcomes import record_blocked_entry
        record_blocked_entry(
            gate_name="gate_edge", ticker="BAS.DE",
            entry_price=53.0, stop_loss=52.0, take_profit=None,
            setup_type="breakout_resistance", regime="RISK_ON",
        )
        self.assertEqual(len(self._pending()), 0)

    def test_valid_block_records(self):
        from core.llm.telemetry.outcomes import record_blocked_entry
        record_blocked_entry(
            gate_name="gate_edge", ticker="BAS.DE",
            entry_price=53.0, stop_loss=52.0, take_profit=[55.0, 57.5],
            setup_type="breakout_resistance", regime="RISK_ON",
        )
        recs = self._pending()
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["gate"], "gate_edge")
        self.assertEqual(r["ticker"], "BAS.DE")
        self.assertEqual(r["take_profit"], [55.0, 57.5])
        self.assertEqual(r["regime"], "RISK_ON")

    def test_scalar_tp_normalized_to_list(self):
        from core.llm.telemetry.outcomes import record_blocked_entry
        record_blocked_entry(
            gate_name="gate_edge", ticker="BAS.DE",
            entry_price=53.0, stop_loss=52.0, take_profit=55.0,
            setup_type="breakout_resistance", regime="RISK_ON",
        )
        recs = self._pending()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["take_profit"], [55.0])


class TestFalseNegativeRates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._patch_resolved = patch(
            "core.llm.telemetry.outcomes._RESOLVED_PATH",
            self._tmp_path / "resolved.jsonl",
        )
        self._patch_resolved.start()

    def tearDown(self):
        self._patch_resolved.stop()
        self._tmp.cleanup()

    def _seed(self, records: list[dict]) -> None:
        path = self._tmp_path / "resolved.jsonl"
        with path.open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_empty_returns_empty(self):
        from core.llm.telemetry.outcomes import gate_false_negative_rates
        self.assertEqual(gate_false_negative_rates(), {})

    def test_aggregates_per_gate(self):
        from core.llm.telemetry.outcomes import gate_false_negative_rates
        self._seed([
            # gate_edge: 3 records, 1 would-win, 1 would-lose, 1 ambiguous
            {"gate": "gate_edge", "would_win": True},
            {"gate": "gate_edge", "would_win": False},
            {"gate": "gate_edge", "would_win": None},
            # gate_rs: 2 records, 2 would-win
            {"gate": "gate_rs", "would_win": True},
            {"gate": "gate_rs", "would_win": True},
        ])
        out = gate_false_negative_rates()
        self.assertEqual(out["gate_edge"]["total"], 3)
        self.assertEqual(out["gate_edge"]["would_win"], 1)
        self.assertEqual(out["gate_edge"]["ambiguous"], 1)
        self.assertAlmostEqual(out["gate_edge"]["false_negative_rate"], 0.333, places=2)
        self.assertEqual(out["gate_rs"]["total"], 2)
        self.assertEqual(out["gate_rs"]["false_negative_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
