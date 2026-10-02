"""`dispatch.py` の出力取り込みと CLI 確認のテスト。CLI を起こさずに走る。

守っている規則は 1 つである。**最終回答は「最後のツール呼び出しより後に出た
アシスタントのメッセージ」をすべて連結したものであり、最後の 1 通ではない。**
モデルが長い成果を複数のメッセージへ分けて出しても、全部を拾う。

実行:
    python -m unittest discover -s .agents/skills/multi-agent-dispatch/scripts -p "test_*.py"
"""

import importlib.util
import json
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("dispatch", Path(__file__).with_name("dispatch.py"))
dispatch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispatch)


def _line(**kw):
    return json.dumps(kw, ensure_ascii=False)


def _assistant(*blocks):
    return _line(type="assistant", message={"content": list(blocks)})


def _text(t):
    return {"type": "text", "text": t}


def _tool_use():
    return {"type": "tool_use", "name": "Read", "input": {}}


def _claude_result(text):
    return _line(type="result", subtype="success", result=text,
                 session_id="S1", total_cost_usd=1.25, usage={"output_tokens": 9})


class ClaudeStreamTests(unittest.TestCase):
    def test_splitted_answer_is_joined(self):
        # ツール呼び出しの後に 2 通へ分けて出した成果を両方拾う
        out = "\n".join([
            _line(type="system", subtype="init"),
            _assistant(_text("まず設計書を読みます。")),
            _assistant(_tool_use()),
            _line(type="user", message={"content": [{"type": "tool_result", "content": "..."}]}),
            _assistant(_text("# 計画書\n\n## Goal\n")),
            _assistant(_text("## Task 1\n本文。")),
            _claude_result("## Task 1\n本文。"),  # result は末尾の 1 通しか運ばない
        ])
        final, text = dispatch._parse_claude_stream(out)
        self.assertEqual(text, "# 計画書\n\n## Goal\n## Task 1\n本文。")
        self.assertEqual(final["session_id"], "S1")

    def test_narration_before_a_tool_call_is_dropped(self):
        out = "\n".join([_assistant(_text("調べます。")), _assistant(_tool_use()),
                         _assistant(_text("答え。")), _claude_result("答え。")])
        self.assertEqual(dispatch._parse_claude_stream(out)[1], "答え。")

    def test_single_message_is_unchanged(self):
        out = "\n".join([_assistant(_text("短い答え。")), _claude_result("短い答え。")])
        self.assertEqual(dispatch._parse_claude_stream(out)[1], "短い答え。")

    def test_falls_back_to_result_when_no_assistant_events(self):
        self.assertEqual(dispatch._parse_claude_stream(_claude_result("これだけ"))[1], "これだけ")

    def test_non_json_lines_are_skipped(self):
        out = "\n".join(["Ignoring 1 permissions.allow entry from .claude/settings.json",
                         _assistant(_text("本文")), _claude_result("本文")])
        self.assertEqual(dispatch._parse_claude_stream(out)[1], "本文")

    def test_missing_result_event_is_a_failure(self):
        self.assertIsNone(dispatch._parse_claude_stream(_assistant(_text("x")))[0])

    def test_continuation_joins_without_a_separator(self):
        # 式の途中で切れることがある。区切り文字を入れるとコードが壊れる
        out = "\n".join([_assistant(_tool_use()), _assistant(_text("CHECK (a ==")),
                         _assistant(_text(" b);")), _claude_result(" b);")])
        self.assertEqual(dispatch._parse_claude_stream(out)[1], "CHECK (a == b);")


def _codex(stdout):
    """call_codex の取り込み部分だけを、CLI を起こさずに走らせる。"""
    def fake_run(cmd, prompt, cwd, timeout):
        return 0, stdout, ""

    original_run, original_find = dispatch._run, dispatch.find_codex
    dispatch._run, dispatch.find_codex = fake_run, lambda: "codex"
    try:
        return dispatch.call_codex("m", "high", "p", Path("."), 60, "read-only")
    finally:
        dispatch._run, dispatch.find_codex = original_run, original_find


def _item(itype, **kw):
    return _line(type="item.completed", item=dict(type=itype, **kw))


class CodexStreamTests(unittest.TestCase):
    def test_splitted_answer_is_joined(self):
        out = "\n".join([
            _line(type="thread.started", thread_id="T1"),
            _item("agent_message", text="調べます。"),
            _item("command_execution", command="rg foo"),
            _item("agent_message", text="# 計画書\n"),
            _item("reasoning", text="（推論）"),
            _item("agent_message", text="## Task 1\n本文。"),
            _line(type="turn.completed", usage={"output_tokens": 3}),
        ])
        r = _codex(out)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.text, "# 計画書\n## Task 1\n本文。")
        self.assertEqual(r.session_id, "T1")
        self.assertEqual(r.usage, {"output_tokens": 3})

    def test_single_message_is_unchanged(self):
        out = "\n".join([_line(type="thread.started", thread_id="T1"),
                         _item("agent_message", text="短い答え。"),
                         _line(type="turn.completed", usage={})])
        self.assertEqual(_codex(out).text, "短い答え。")

    def test_error_item_breaks_the_run_of_messages(self):
        out = "\n".join([_item("agent_message", text="途中まで書いた分"),
                         _item("error", message="壊れた"),
                         _item("agent_message", text="やり直した答え。"),
                         _line(type="turn.completed", usage={})])
        r = _codex(out)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.text, "やり直した答え。")

    def test_empty_final_message_is_a_failure(self):
        out = "\n".join([_item("error", message="壊れた"),
                         _line(type="turn.failed", error={"m": 1})])
        self.assertFalse(_codex(out).ok)


class AuthParseTests(unittest.TestCase):
    def test_claude_logged_in(self):
        ok, msg = dispatch.parse_claude_auth('{"loggedIn": true, "authMethod": "claude.ai"}')
        self.assertTrue(ok)
        self.assertIn("claude.ai", msg)

    def test_claude_logged_out_or_garbage(self):
        self.assertFalse(dispatch.parse_claude_auth('{"loggedIn": false}')[0])
        self.assertFalse(dispatch.parse_claude_auth("not json")[0])

    def test_codex_logged_in(self):
        ok, msg = dispatch.parse_codex_auth(0, "Logged in using ChatGPT\n")
        self.assertTrue(ok)
        self.assertEqual(msg, "Logged in using ChatGPT")

    def test_codex_logged_in_line_among_warnings(self):
        ok, _ = dispatch.parse_codex_auth(0, "2026-01-01 ERROR failed to load skill x\nLogged in using API key\n")
        self.assertTrue(ok)

    def test_codex_not_logged_in(self):
        self.assertFalse(dispatch.parse_codex_auth(1, "Not logged in\n")[0])
        self.assertFalse(dispatch.parse_codex_auth(0, "")[0])


class TemplateTests(unittest.TestCase):
    def test_templates_have_no_unfilled_placeholders(self):
        run = {"agents": {"A": {}, "B": {}, "C": {}}}
        common = dict(TASK="t", RUN_DIR="d", **dispatch.agent_vars(run))
        rendered = [
            dispatch.render("task.md", MODE_NOTE="m", **common),
            dispatch.render("review.md", RESULTS="r", **common),
            dispatch.render("synthesis.md", RESULTS="r", REVIEWS="v", AGENT_TABLE="tbl", **common),
        ]
        for text in rendered:
            self.assertNotIn("$", text)
        self.assertIn("3 体のエージェント", rendered[0])
        self.assertIn("2 体の成果", rendered[1])
        self.assertIn("エージェント A、エージェント B、エージェント C", rendered[2])


if __name__ == "__main__":
    unittest.main()
