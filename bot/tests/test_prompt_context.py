"""Tests for the morning candidate-shortlist pre-filter (pure logic, no network)."""

import unittest

import config
from core.llm.prompt.context import _shortlist_candidates


def _t(tier, state, red=0, error=False):
    if error:
        return {"error": "boom"}
    return {"state": {"quality_tier": tier, "entry_state": state,
                      "red_flags": ["f"] * red}}


class ShortlistTest(unittest.TestCase):
    def test_protected_always_kept_even_if_low_ranked(self):
        md = {
            "OPEN.DE": _t("C", "UNKNOWN", red=3),   # weak, but protected
            "A1.DE": _t("A", "VALID"),
            "A2.DE": _t("A", "EARLY"),
        }
        out = _shortlist_candidates(md, protected={"OPEN.DE"}, cap=1)
        self.assertIn("OPEN.DE", out)                # protected survives cap
        self.assertEqual(len([k for k in out if k != "OPEN.DE"]), 1)  # cap honored

    def test_cap_keeps_highest_ranked(self):
        md = {
            "HI.DE": _t("A", "VALID"),               # 30 + 6 = 36
            "MID.DE": _t("B", "VALID"),              # 20 + 6 = 26
            "LO.DE": _t("C", "LATE", red=1),         # 10 + 3 - 2 = 11
        }
        out = _shortlist_candidates(md, protected=set(), cap=2)
        self.assertEqual(set(out), {"HI.DE", "MID.DE"})

    def test_red_flags_penalize_rank(self):
        md = {
            "CLEAN.DE": _t("A", "VALID", red=0),     # 36
            "FLAGGED.DE": _t("A", "VALID", red=4),   # 36 - 8 = 28
        }
        out = _shortlist_candidates(md, protected=set(), cap=1)
        self.assertEqual(set(out), {"CLEAN.DE"})

    def test_unprotected_error_rows_dropped(self):
        # A broken ticker that isn't an open position is useless to the LLM — drop it.
        md = {
            "ERR.DE": _t(None, None, error=True),
            "OK.DE": _t("A", "VALID"),
        }
        out = _shortlist_candidates(md, protected=set(), cap=5)
        self.assertNotIn("ERR.DE", out)
        self.assertIn("OK.DE", out)

    def test_protected_error_row_kept(self):
        # But a protected (open/watch) ticker stays even if its fetch errored —
        # exit/breakout context must not silently vanish on a data hiccup.
        md = {"POS.DE": _t(None, None, error=True), "OK.DE": _t("A", "VALID")}
        out = _shortlist_candidates(md, protected={"POS.DE"}, cap=5)
        self.assertIn("POS.DE", out)

    def test_cap_uses_config_default_sanity(self):
        # The production cap must be a positive int — guards against a config typo.
        self.assertIsInstance(config.MORNING_CANDIDATE_SHORTLIST, int)
        self.assertGreater(config.MORNING_CANDIDATE_SHORTLIST, 0)


if __name__ == "__main__":
    unittest.main()
