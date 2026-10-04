"""`ask_claude.py` の取り込み規則、認証判定、モデル解決、テンプレートのテスト。CLI を起こさずに走る。

実行:
    python -m unittest discover -s .agents/skills/ask-claude/scripts -p "test_*.py"
"""

import importlib.util
import json
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("ask_claude", Path(__file__).with_name("ask_claude.py"))
ask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ask)


def _line(**kw):
    return json.dumps(kw, ensure_ascii=False)


def _assistant(*blocks):
    return _line(type="assistant", message={"content": list(blocks)})


def _text(t):
    return {"type": "text", "text": t}


def _tool_use():
    return {"type": "tool_use", "name": "Read", "input": {}}


def _result(text):
    return _line(type="result", subtype="success", result=text, session_id="S1",
                 total_cost_usd=0.5, usage={"output_tokens": 9})


class StreamTests(unittest.TestCase):
    def test_final_answer_joins_messages_after_last_tool(self):
        out = "\n".join([
            _assistant(_text("調べます。")), _assistant(_tool_use()),
            _assistant(_text("# 結論\n")), _assistant(_text("本文。")), _result("本文。"),
        ])
        final, text = ask._parse_claude_stream(out)
        self.assertEqual(text, "# 結論\n本文。")
        self.assertEqual(final["session_id"], "S1")

    def test_non_json_lines_are_skipped_and_missing_result_fails(self):
        out = "\n".join(["Ignoring 1 permissions.allow entry", _assistant(_text("本文")), _result("本文")])
        self.assertEqual(ask._parse_claude_stream(out)[1], "本文")
        self.assertIsNone(ask._parse_claude_stream(_assistant(_text("x")))[0])

    def test_call_claude_reports_error_result(self):
        original = ask._run
        ask._run = lambda cmd, prompt, cwd, timeout: (1, _line(type="result", is_error=True, result="oops", session_id="S2"), "")
        try:
            r = ask.call_claude("claude", "m", "high", "p", Path("."), 60, "auto", ["Read"])
        finally:
            ask._run = original
        self.assertFalse(r.ok)
        self.assertIn("oops", r.error)


class AuthAndModelTests(unittest.TestCase):
    def test_claude_auth(self):
        self.assertTrue(ask.parse_claude_auth('{"loggedIn": true, "authMethod": "claude.ai"}')[0])
        self.assertFalse(ask.parse_claude_auth('{"loggedIn": false}')[0])
        self.assertFalse(ask.parse_claude_auth("not json")[0])

    def test_model_aliases_and_ids(self):
        self.assertEqual(ask.resolve_model("fable"), ("claude-fable-5-1", "Claude Fable 5.1"))
        self.assertEqual(ask.resolve_model("OPUS"), ("claude-opus-5-5", "Claude Opus 5.5"))
        self.assertEqual(ask.resolve_model("claude-sonnet-5")[0], "claude-sonnet-5")
        with self.assertRaises(SystemExit):
            ask.resolve_model("haiku")


class ProxyParseTests(unittest.TestCase):
    def test_proxy_target(self):
        self.assertIsNone(ask._proxy_target({}))
        self.assertEqual(ask._proxy_target({"HTTPS_PROXY": "http://proxy.example:3128"}), ("proxy.example", 3128))
        self.assertEqual(ask._proxy_target({"https_proxy": "http://u:p@proxy.example/"}), ("proxy.example", 443))


class StatusFileTests(unittest.TestCase):
    def test_status_roundtrip_keeps_out_field(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "answer.md"
            ask.write_status(out, state="running", pid=1, out=str(out), model="m")
            st = ask.read_status(out)
            self.assertEqual(st["state"], "running")
            self.assertEqual(st["out"], str(out))
            self.assertIsNone(ask.read_status(Path(d) / "none.md"))


class TemplateTests(unittest.TestCase):
    def test_prompt_has_no_unfilled_placeholders(self):
        for mode in ("analysis", "edit"):
            text = ask.build_prompt("依頼本文", mode)
            self.assertNotIn("$", text)
            self.assertIn("依頼本文", text)
        self.assertIn("書き換えない", ask.build_prompt("t", "analysis"))
        self.assertIn("コミットはしない", ask.build_prompt("t", "edit"))


if __name__ == "__main__":
    unittest.main()
