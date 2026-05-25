"""Tests for agents._lib.runner — subprocess mock + envelope parsing.

Avoids real `claude` CLI invocation. Validates:
- envelope-key handling (structured_output preferred, fallback to result)
- malformed-JSON detection
- action → tool_use block mapping
- timeout path
- exit-code != 0 path
- usage extraction
"""

import json
import subprocess
import unittest
from unittest.mock import patch

from agents._lib.runner import (
    AgentRunError, _wrap_actions_schema, call_claude_agent,
)


_DUMMY_TOOL = {
    "name": "submit_pass",
    "input_schema": {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "additionalProperties": False,
    },
}


def _fake_proc(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class TestActionsSchema(unittest.TestCase):
    def test_schema_oneof_per_tool(self):
        tools = [
            {"name": "tool_a", "input_schema": {"type": "object", "properties": {}}},
            {"name": "tool_b", "input_schema": {"type": "object", "properties": {}}},
        ]
        sch = _wrap_actions_schema(tools)
        self.assertEqual(sch["type"], "object")
        self.assertIn("actions", sch["properties"])
        one_of = sch["properties"]["actions"]["items"]["oneOf"]
        self.assertEqual(len(one_of), 2)
        self.assertEqual(one_of[0]["properties"]["tool"]["const"], "tool_a")
        self.assertEqual(one_of[1]["properties"]["tool"]["const"], "tool_b")


class TestEnvelopeParsing(unittest.TestCase):
    def setUp(self):
        # Skip the real CLI binary lookup
        self._bin_patch = patch(
            "agents._lib.runner._resolve_claude_bin",
            return_value="/fake/claude",
        )
        self._bin_patch.start()
        self.addCleanup(self._bin_patch.stop)

    def _run(self, envelope: dict | str, **kw):
        stdout = json.dumps(envelope) if isinstance(envelope, dict) else envelope
        with patch("subprocess.run", return_value=_fake_proc(stdout)):
            return call_claude_agent(
                mode="test", system_prompt="sys", user_message="msg",
                tools=[_DUMMY_TOOL], max_tokens=500, model="claude-haiku-4-5",
                force_any_tool=True, **kw,
            )

    def test_structured_output_key_preferred(self):
        env = {
            "structured_output": {
                "narrative": "n",
                "actions": [{"tool": "submit_pass", "input": {"reason": "x"}}],
            },
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
        resp = self._run(env)
        tool_blocks = [b for b in resp.content if b.type == "tool_use"]
        self.assertEqual(len(tool_blocks), 1)
        self.assertEqual(tool_blocks[0].name, "submit_pass")
        self.assertEqual(tool_blocks[0].input, {"reason": "x"})
        self.assertEqual(resp.usage.input_tokens, 10)
        self.assertEqual(resp.usage.output_tokens, 20)
        self.assertEqual(resp.stop_reason, "tool_use")

    def test_legacy_result_string_fallback(self):
        # Older CLI versions returned the JSON as a string under "result".
        env = {
            "result": json.dumps({
                "actions": [{"tool": "submit_pass", "input": {"reason": "y"}}],
            }),
        }
        resp = self._run(env)
        self.assertEqual(resp.content[0].name, "submit_pass")

    def test_code_fence_stripping(self):
        env = {
            "result": "```json\n" + json.dumps({
                "actions": [{"tool": "submit_pass", "input": {"reason": "z"}}],
            }) + "\n```",
        }
        resp = self._run(env)
        self.assertEqual(resp.content[0].name, "submit_pass")

    def test_narrative_emitted_as_text_block(self):
        env = {
            "structured_output": {
                "narrative": "Market closed weekend.",
                "actions": [{"tool": "submit_pass", "input": {"reason": "weekend"}}],
            },
        }
        resp = self._run(env)
        text_blocks = [b for b in resp.content if b.type == "text"]
        self.assertEqual(len(text_blocks), 1)
        self.assertEqual(text_blocks[0].text, "Market closed weekend.")

    def test_no_narrative_no_text_block(self):
        env = {"structured_output": {"actions": [
            {"tool": "submit_pass", "input": {"reason": "x"}},
        ]}}
        resp = self._run(env)
        self.assertEqual([b for b in resp.content if b.type == "text"], [])

    def test_empty_actions_yields_end_turn(self):
        env = {"structured_output": {"actions": []}}
        resp = self._run(env)
        self.assertEqual(resp.stop_reason, "end_turn")
        self.assertEqual([b for b in resp.content if b.type == "tool_use"], [])

    def test_malformed_json_raises(self):
        with patch("subprocess.run", return_value=_fake_proc("not-json{")):
            # Treats the whole stdout as a JSON-parse-fail envelope, then
            # parses the (empty) payload — should not raise on this path.
            # Actual error case is when envelope-parse succeeds but payload
            # is a string that can't be parsed. Construct that:
            pass
        bad = {"result": "{invalid json"}
        with self.assertRaises(AgentRunError) as ctx:
            self._run(bad)
        self.assertIn("non-JSON", str(ctx.exception))

    def test_nonzero_exit_raises(self):
        with patch("subprocess.run", return_value=_fake_proc("", returncode=1, stderr="boom")):
            with self.assertRaises(AgentRunError) as ctx:
                call_claude_agent(
                    mode="t", system_prompt="s", user_message="u",
                    tools=[_DUMMY_TOOL], max_tokens=500,
                    model="claude-haiku-4-5", force_any_tool=True,
                )
            self.assertIn("exit=1", str(ctx.exception))

    def test_timeout_raises(self):
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=1)):
            with self.assertRaises(AgentRunError) as ctx:
                call_claude_agent(
                    mode="t", system_prompt="s", user_message="u",
                    tools=[_DUMMY_TOOL], max_tokens=500,
                    model="claude-haiku-4-5", force_any_tool=True,
                    timeout_seconds=1,
                )
            self.assertIn("timed out", str(ctx.exception))

    def test_requires_tools(self):
        with self.assertRaises(AgentRunError) as ctx:
            call_claude_agent(
                mode="t", system_prompt="s", user_message="u",
                tools=None, max_tokens=500, model="claude-haiku-4-5",
                force_any_tool=True,
            )
        self.assertIn("requires tools", str(ctx.exception))


class TestModelAliasing(unittest.TestCase):
    def test_full_name_mapped_to_alias(self):
        from agents._lib.runner import _normalize_model
        self.assertEqual(_normalize_model("claude-sonnet-4-6"), "sonnet")
        self.assertEqual(_normalize_model("claude-haiku-4-5"), "haiku")

    def test_unknown_passes_through(self):
        from agents._lib.runner import _normalize_model
        self.assertEqual(_normalize_model("claude-future-99"), "claude-future-99")


if __name__ == "__main__":
    unittest.main()
