"""Tests for config/setup_profiles.py — setup-type behavior matrix."""

import unittest

import config
from config.setup_profiles import (
    SETUP_PROFILES, SetupProfile, get_profile,
)


class TestSetupProfileLookup(unittest.TestCase):
    def test_known_setup_returns_profile(self):
        p = get_profile("mean_reversion")
        self.assertIsInstance(p, SetupProfile)
        self.assertEqual(p.name, "mean_reversion")

    def test_case_insensitive_lookup(self):
        a = get_profile("Mean_Reversion")
        b = get_profile("MEAN_REVERSION")
        self.assertEqual(a.name, b.name)
        self.assertEqual(a.name, "mean_reversion")

    def test_unknown_setup_returns_default(self):
        p = get_profile("frobnicate")
        self.assertEqual(p.name, "default")
        self.assertFalse(p.rs_override)
        self.assertEqual(p.min_confluence_offset, 0)
        self.assertFalse(p.requires_breakout_volume)
        self.assertFalse(p.bypass_earnings_block)

    def test_none_setup_returns_default(self):
        p = get_profile(None)
        self.assertEqual(p.name, "default")

    def test_empty_string_returns_default(self):
        p = get_profile("")
        self.assertEqual(p.name, "default")


class TestMeanReversionFamily(unittest.TestCase):
    """All mean-rev family setups must share: rs_override + confluence-2."""

    FAMILY = ("mean_reversion", "reversal_oversold", "gap_fill", "pre_breakout_squeeze")

    def test_all_have_rs_override(self):
        for setup in self.FAMILY:
            with self.subTest(setup=setup):
                self.assertTrue(get_profile(setup).rs_override)

    def test_all_have_confluence_relaxation(self):
        for setup in self.FAMILY:
            with self.subTest(setup=setup):
                self.assertEqual(get_profile(setup).min_confluence_offset, -2)

    def test_none_require_breakout_volume(self):
        for setup in self.FAMILY:
            with self.subTest(setup=setup):
                self.assertFalse(get_profile(setup).requires_breakout_volume)

    def test_none_bypass_earnings(self):
        for setup in self.FAMILY:
            with self.subTest(setup=setup):
                self.assertFalse(get_profile(setup).bypass_earnings_block)


class TestBreakoutResistance(unittest.TestCase):
    def test_requires_breakout_volume(self):
        p = get_profile("breakout_resistance")
        self.assertTrue(p.requires_breakout_volume)

    def test_no_rs_override(self):
        p = get_profile("breakout_resistance")
        self.assertFalse(p.rs_override)

    def test_no_confluence_relax(self):
        p = get_profile("breakout_resistance")
        self.assertEqual(p.min_confluence_offset, 0)


class TestEarningsDrift(unittest.TestCase):
    def test_bypasses_earnings_block(self):
        p = get_profile("earnings_drift")
        self.assertTrue(p.bypass_earnings_block)

    def test_no_other_overrides(self):
        p = get_profile("earnings_drift")
        self.assertFalse(p.rs_override)
        self.assertEqual(p.min_confluence_offset, 0)
        self.assertFalse(p.requires_breakout_volume)


class TestProfilesAreFrozen(unittest.TestCase):
    """Frozen dataclass — accidental mutation forbidden."""

    def test_frozen(self):
        p = get_profile("mean_reversion")
        with self.assertRaises(Exception):
            p.rs_override = False  # type: ignore[misc]


class TestExposureViaConfigModule(unittest.TestCase):
    """SETUP_PROFILES + get_profile reachable via `import config`."""

    def test_via_config_namespace(self):
        self.assertIs(config.get_profile, get_profile)
        self.assertIs(config.SETUP_PROFILES, SETUP_PROFILES)


if __name__ == "__main__":
    unittest.main()
