"""Tests for agents._lib.skill_runner — frontmatter + envelope parse + run.

No real `claude` CLI invocation.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents._lib import skill_runner
from agents._lib.runner import AgentRunError


def _fake_proc(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class TestFrontmatterParse(unittest.TestCase):
    def test_full_frontmatter_parses(self):
        text = (
            "---\n"
            "name: test-skill\n"
            "model: sonnet\n"
            "max_tokens: 2048\n"
            "timeout_seconds: 90\n"
            "---\n"
            "system prompt body"
        )
        meta, body = skill_runner._parse_frontmatter(text)
        self.assertEqual(meta["name"], "test-skill")
        self.assertEqual(meta["model"], "sonnet")
        self.assertEqual(meta["max_tokens"], "2048")
        self.assertEqual(body, "system prompt body")

    def test_no_frontmatter(self):
        meta, body = skill_runner._parse_frontmatter("plain body only")
        self.assertEqual(meta, {})
        self.assertEqual(body, "plain body only")


class TestLoadSkill(unittest.TestCase):
    def setUp(self):
        # Use a temp skills dir so we don't depend on the real .md files
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        # Patch skill cache + dir
        self._sd = patch("agents._lib.skill_runner._SKILLS_DIR", self.tmp_path)
        self._sd.start()
        self.addCleanup(self._sd.stop)
        # Clear module-level cache between tests
        skill_runner._SKILL_CACHE.clear()
        self.addCleanup(skill_runner._SKILL_CACHE.clear)

    def test_loads_and_caches(self):
        skill_file = self.tmp_path / "alpha.md"
        skill_file.write_text("---\nname: alpha\nmodel: haiku\n---\nbody-x")
        spec = skill_runner.load_skill("alpha")
        self.assertEqual(spec.name, "alpha")
        self.assertEqual(spec.model, "haiku")
        self.assertEqual(spec.system_prompt, "body-x")
        # Second load returns cached
        spec2 = skill_runner.load_skill("alpha")
        self.assertIs(spec, spec2)

    def test_missing_skill_raises(self):
        with self.assertRaises(AgentRunError):
            skill_runner.load_skill("does-not-exist")


class TestRunSkill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        (self.tmp_path / "x.md").write_text(
            "---\nname: x\nmodel: haiku\ntimeout_seconds: 60\n---\nyou are x")
        self._sd = patch("agents._lib.skill_runner._SKILLS_DIR", self.tmp_path)
        self._sd.start()
        self.addCleanup(self._sd.stop)
        skill_runner._SKILL_CACHE.clear()
        self.addCleanup(skill_runner._SKILL_CACHE.clear)
        self._bin = patch("agents._lib.skill_runner._resolve_claude_bin",
                          return_value="/fake/claude")
        self._bin.start()
        self.addCleanup(self._bin.stop)

    def test_happy_path_returns_result_markdown(self):
        env = {"result": "## OK\nno issues", "usage": {"input_tokens": 5, "output_tokens": 10}}
        with patch("subprocess.run", return_value=_fake_proc(json.dumps(env))):
            result = skill_runner.run_skill("x", "scan input")
        self.assertEqual(result, "## OK\nno issues")

    def test_timeout_raises(self):
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=1)):
            with self.assertRaises(AgentRunError) as ctx:
                skill_runner.run_skill("x", "scan input")
            self.assertIn("timed out", str(ctx.exception))

    def test_nonzero_exit_raises(self):
        with patch("subprocess.run",
                   return_value=_fake_proc("", returncode=1, stderr="boom")):
            with self.assertRaises(AgentRunError) as ctx:
                skill_runner.run_skill("x", "scan input")
            self.assertIn("exit=1", str(ctx.exception))

    def test_empty_result_string(self):
        env = {"result": "", "usage": {"input_tokens": 1, "output_tokens": 0}}
        with patch("subprocess.run", return_value=_fake_proc(json.dumps(env))):
            result = skill_runner.run_skill("x", "scan input")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
