"""Tests for tools/tuning_audit.py — diff parser + replayability filter.

git-diff invocation + replay-compare are exercised via direct calls in the
manual CLI; tests cover the deterministic-logic pieces.
"""

import unittest


class TestParseValue(unittest.TestCase):
    def test_int(self):
        from tools.tuning_audit import _safe_parse_value
        self.assertEqual(_safe_parse_value("42"), 42)

    def test_float(self):
        from tools.tuning_audit import _safe_parse_value
        self.assertEqual(_safe_parse_value("0.06"), 0.06)

    def test_negative_float(self):
        from tools.tuning_audit import _safe_parse_value
        self.assertEqual(_safe_parse_value("-1.5"), -1.5)

    def test_bool(self):
        from tools.tuning_audit import _safe_parse_value
        self.assertEqual(_safe_parse_value("True"), True)
        self.assertEqual(_safe_parse_value("False"), False)

    def test_string(self):
        from tools.tuning_audit import _safe_parse_value
        self.assertEqual(_safe_parse_value('"SPY5.DE"'), "SPY5.DE")
        self.assertEqual(_safe_parse_value("'foo'"), "foo")

    def test_container_returns_none(self):
        from tools.tuning_audit import _safe_parse_value
        self.assertIsNone(_safe_parse_value("{'a': 1}"))
        self.assertIsNone(_safe_parse_value("[1, 2, 3]"))
        self.assertIsNone(_safe_parse_value("(9, 0, 9, 10)"))


class TestParseDiff(unittest.TestCase):
    def test_single_int_change(self):
        from tools.tuning_audit import parse_diff
        diff = "\n".join([
            "diff --git a/config/risk.py b/config/risk.py",
            "@@ -10,1 +10,1 @@",
            "-MAX_POSITION_SIZE_PERCENT = 30.0",
            "+MAX_POSITION_SIZE_PERCENT = 20.0",
        ])
        changes = parse_diff(diff)
        self.assertEqual(len(changes), 1)
        c = changes[0]
        self.assertEqual(c.name, "MAX_POSITION_SIZE_PERCENT")
        self.assertEqual(c.old_value, 30.0)
        self.assertEqual(c.new_value, 20.0)

    def test_change_with_inline_comment(self):
        from tools.tuning_audit import parse_diff
        diff = "\n".join([
            "-MIN_EXPECTED_EDGE = 0.04          # was 0.04",
            "+MIN_EXPECTED_EDGE = 0.06          # 2026-05-12 bump",
        ])
        changes = parse_diff(diff)
        self.assertEqual(len(changes), 1)
        c = changes[0]
        self.assertEqual(c.old_value, 0.04)
        self.assertEqual(c.new_value, 0.06)

    def test_unchanged_skipped(self):
        from tools.tuning_audit import parse_diff
        diff = "\n".join([
            "-MAX_POSITION_SIZE_PERCENT = 20.0",
            "+MAX_POSITION_SIZE_PERCENT = 20.0",
        ])
        self.assertEqual(parse_diff(diff), [])

    def test_only_added_or_only_removed_skipped(self):
        # New constant introduced (only '+'): not a change.
        from tools.tuning_audit import parse_diff
        diff = "+NEW_CONSTANT = 99"
        self.assertEqual(parse_diff(diff), [])

    def test_multiple_changes(self):
        from tools.tuning_audit import parse_diff
        diff = "\n".join([
            "-MIN_EXPECTED_EDGE = 0.04",
            "+MIN_EXPECTED_EDGE = 0.06",
            "-MAX_POSITION_SIZE_PERCENT = 30.0",
            "+MAX_POSITION_SIZE_PERCENT = 20.0",
        ])
        changes = parse_diff(diff)
        self.assertEqual(len(changes), 2)
        names = {c.name for c in changes}
        self.assertEqual(names, {"MIN_EXPECTED_EDGE", "MAX_POSITION_SIZE_PERCENT"})

    def test_lower_case_lines_ignored(self):
        """Constants follow ALL_CAPS convention; module-private (_foo) ignored."""
        from tools.tuning_audit import parse_diff
        diff = "\n".join([
            "-_internal_var = 1",
            "+_internal_var = 2",
            "-some_function(x):",
            "+some_function(y):",
        ])
        self.assertEqual(parse_diff(diff), [])


class TestStagedFlag(unittest.TestCase):
    """get_config_diff(staged=True) must use `git diff --cached`."""

    def test_staged_invokes_cached_flag(self):
        from unittest.mock import patch, MagicMock
        from tools.tuning_audit import get_config_diff

        mock_proc = MagicMock(stdout="", returncode=0)
        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            get_config_diff(staged=True)
            args, _ = mock_run.call_args
            cmd = args[0]
            self.assertIn("--cached", cmd)
            self.assertNotIn("HEAD", cmd)

    def test_non_staged_passes_rev(self):
        from unittest.mock import patch, MagicMock
        from tools.tuning_audit import get_config_diff

        mock_proc = MagicMock(stdout="", returncode=0)
        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            get_config_diff(rev="HEAD~3", staged=False)
            args, _ = mock_run.call_args
            cmd = args[0]
            self.assertIn("HEAD~3", cmd)
            self.assertNotIn("--cached", cmd)


class TestReplayableFilter(unittest.TestCase):
    def _change(self, old, new):
        from tools.tuning_audit import ConstantChange
        return ConstantChange(
            name="X", old_raw=str(old), new_raw=str(new),
            old_value=old, new_value=new,
        )

    def test_numeric_replayable(self):
        from tools.tuning_audit import _is_replayable
        self.assertTrue(_is_replayable(self._change(30.0, 20.0)))
        self.assertTrue(_is_replayable(self._change(5, 6)))

    def test_bool_not_replayable(self):
        from tools.tuning_audit import _is_replayable
        # bool subclasses int in Python — explicit guard.
        self.assertFalse(_is_replayable(self._change(True, False)))

    def test_string_not_replayable(self):
        from tools.tuning_audit import _is_replayable
        self.assertFalse(_is_replayable(self._change("SPY5.DE", "QQQ.DE")))

    def test_container_change_not_replayable(self):
        from tools.tuning_audit import _is_replayable
        self.assertFalse(_is_replayable(self._change(None, 42)))
        self.assertFalse(_is_replayable(self._change(42, None)))


if __name__ == "__main__":
    unittest.main()
