"""`ask_claude.py` の取り込み規則、認証判定、モデル解決、テンプレートのテスト。CLI を起こさずに走る。

実行:
    python -m unittest discover -s .agents/skills/ask-claude/scripts -p "test_*.py"
"""

import contextlib
import importlib.util
import io
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


class FallbackTests(unittest.TestCase):
    def _limited(self, tool=False):
        events = [_assistant(_tool_use())] if tool else []
        events += [_line(type="assistant", error="rate_limit", message={"content": [_text("You've hit your limit")]}),
                   _line(type="result", is_error=True, api_error_status=429, result="You've hit your limit",
                         session_id="S1")]
        return "\n".join(events)

    def test_stream_signals(self):
        self.assertEqual(ask._stream_signals(self._limited()), (True, False))
        self.assertEqual(ask._stream_signals(self._limited(tool=True)), (True, True))
        only_result = _line(type="result", is_error=True, api_error_status=429, result="x")
        self.assertEqual(ask._stream_signals(only_result), (True, False))
        ok = "\n".join([_assistant(_tool_use()), _assistant(_text("本文")), _result("本文")])
        self.assertEqual(ask._stream_signals(ok), (False, True))
        other = _line(type="result", is_error=True, api_error_status=500, result="x")
        self.assertEqual(ask._stream_signals(other), (False, False))

    def _ask(self, outputs, *argv):
        """CLI の起動（_run と preflight）を差し替えて ask を走らせ、各呼び出しの（モデル、プロンプト、resume）を返す。"""
        calls = []
        originals = ask._run, ask.preflight

        def fake_run(cmd, prompt, cwd, timeout):
            calls.append((cmd[cmd.index("--model") + 1], prompt, cmd[cmd.index("--resume") + 1] if "--resume" in cmd else None))
            return 0, outputs[len(calls) - 1], ""

        ask._run, ask.preflight = fake_run, lambda: "claude"
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    ask.main(["ask", "--task", "依頼本文", *argv])
                    code = 0
                except SystemExit as e:
                    code = e.code
        finally:
            ask._run, ask.preflight = originals
        return calls, code

    def test_falls_back_to_opus_from_scratch(self):
        ok = "\n".join([_assistant(_text("本文")), _result("本文")])
        calls, code = self._ask([self._limited(), ok])
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in calls], ["claude-fable-5-1", "claude-opus-5-5"])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertIsNone(calls[1][2])

    def test_falls_back_by_resuming_after_tool_use(self):
        ok = "\n".join([_assistant(_text("本文")), _result("本文")])
        calls, code = self._ask([self._limited(tool=True), ok])
        self.assertEqual(code, 0)
        self.assertEqual(calls[1][0], "claude-opus-5-5")
        self.assertEqual(calls[1][1], ask.CONTINUE_PROMPT)
        self.assertEqual(calls[1][2], "S1")

    def test_no_fallback_for_opus_flag_or_other_errors(self):
        self.assertEqual(len(self._ask([self._limited()], "--model", "opus")[0]), 1)
        self.assertEqual(len(self._ask([self._limited()], "--no-fallback")[0]), 1)
        other = _line(type="result", is_error=True, api_error_status=500, result="x", session_id="S1")
        calls, code = self._ask([other])
        self.assertEqual((len(calls), code), (1, 1))

    def test_status_records_fallback(self):
        import tempfile
        ok = "\n".join([_assistant(_text("本文")), _result("本文")])
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "answer.md"
            self._ask([self._limited(), ok], "--out", str(out))
            st = ask.read_status(out)
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["model"], "claude-opus-5-5")
        self.assertEqual(st["fallback"]["from"], "claude-fable-5-1")


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
