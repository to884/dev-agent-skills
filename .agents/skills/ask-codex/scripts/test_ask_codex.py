"""`ask_codex.py` の取り込み規則、認証判定、モデル解決、テンプレートのテスト。CLI を起こさずに走る。

実行:
    python -m unittest discover -s .agents/skills/ask-codex/scripts -p "test_*.py"
"""

import importlib.util
import json
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("ask_codex", Path(__file__).with_name("ask_codex.py"))
ask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ask)


def _line(**kw):
    return json.dumps(kw, ensure_ascii=False)


def _item(itype, **kw):
    return _line(type="item.completed", item=dict(type=itype, **kw))


def _codex(stdout):
    original = ask._run
    ask._run = lambda cmd, prompt, cwd, timeout: (0, stdout, "")
    try:
        return ask.call_codex("codex", "m", "high", "p", Path("."), 60, "read-only")
    finally:
        ask._run = original


class StreamTests(unittest.TestCase):
    def test_final_answer_joins_messages_after_last_tool(self):
        out = "\n".join([
            _line(type="thread.started", thread_id="T1"),
            _item("agent_message", text="調べます。"),
            _item("command_execution", command="rg foo"),
            _item("agent_message", text="# 結論\n"),
            _item("reasoning", text="（推論）"),
            _item("agent_message", text="本文。"),
            _line(type="turn.completed", usage={"output_tokens": 3}),
        ])
        r = _codex(out)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.text, "# 結論\n本文。")
        self.assertEqual(r.session_id, "T1")
        self.assertEqual(r.usage, {"output_tokens": 3})

    def test_empty_final_message_is_a_failure(self):
        r = _codex("\n".join([_item("error", message="壊れた"), _line(type="turn.failed", error={"m": 1})]))
        self.assertFalse(r.ok)
        self.assertIn("壊れた", r.error)


class AuthAndModelTests(unittest.TestCase):
    def test_codex_auth(self):
        self.assertTrue(ask.parse_codex_auth(0, "Logged in using ChatGPT\n")[0])
        self.assertTrue(ask.parse_codex_auth(0, "ERROR failed to load skill x\nLogged in using API key\n")[0])
        self.assertFalse(ask.parse_codex_auth(1, "Not logged in\n")[0])
        self.assertFalse(ask.parse_codex_auth(0, "")[0])

    def test_model_aliases_and_ids(self):
        self.assertEqual(ask.resolve_model("astra"), ("gpt-6-astra", "GPT-6 Astra"))
        self.assertEqual(ask.resolve_model("Sol"), ("GPT-6-sol", "GPT-6 Sol"))
        self.assertEqual(ask.resolve_model("GPT-6-sol")[0], "GPT-6-sol")
        with self.assertRaises(SystemExit):
            ask.resolve_model("nova")


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
