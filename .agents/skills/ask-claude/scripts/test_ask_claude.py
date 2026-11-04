"""`ask_claude.py` の取り込み規則、認証判定、モデル解決、テンプレート、review サブコマンドのテスト。CLI を起こさずに走る。

実行:
    python -m unittest discover -s .agents/skills/ask-claude/scripts -p "test_*.py"
"""

import contextlib
import importlib.util
import io
import json
import tempfile
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
        calls, code = self._ask([self._limited(), ok], "--model", "fable")
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in calls], ["claude-fable-5-1", "claude-opus-5-5"])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertIsNone(calls[1][2])

    def test_falls_back_by_resuming_after_tool_use(self):
        ok = "\n".join([_assistant(_text("本文")), _result("本文")])
        calls, code = self._ask([self._limited(tool=True), ok], "--model", "fable")
        self.assertEqual(code, 0)
        self.assertEqual(calls[1][0], "claude-opus-5-5")
        self.assertEqual(calls[1][1], ask.CONTINUE_PROMPT)
        self.assertEqual(calls[1][2], "S1")

    def test_no_fallback_for_opus_flag_or_other_errors(self):
        self.assertEqual(len(self._ask([self._limited()])[0]), 1)
        self.assertEqual(len(self._ask([self._limited()], "--model", "opus")[0]), 1)
        self.assertEqual(len(self._ask([self._limited()], "--model", "fable", "--no-fallback")[0]), 1)
        other = _line(type="result", is_error=True, api_error_status=500, result="x", session_id="S1")
        calls, code = self._ask([other], "--model", "fable")
        self.assertEqual((len(calls), code), (1, 1))

    def test_status_records_fallback(self):
        import tempfile
        ok = "\n".join([_assistant(_text("本文")), _result("本文")])
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "answer.md"
            self._ask([self._limited(), ok], "--model", "fable", "--out", str(out))
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

    def test_review_prompt_has_all_headings(self):
        text = ask.build_prompt(ask.NO_INTENT, "analysis", ask.TEMPLATE_REVIEW, TARGET="- `a.py`")
        self.assertNotIn("$", text)
        for heading in REVIEW_HEADINGS:
            self.assertIn(f"`{heading}`", text)
        self.assertIn("崩せなかった", text)
        self.assertIn("書き換えない", text)


REVIEW_HEADINGS = ("## 判定", "## 致命的・重大な欠陥", "## 崩れる前提", "## 見落としと軽微な問題",
                   "## 検証した事実", "## 崩せなかった点")
_OK = "\n".join([_assistant(_text("## 判定\n採用可")), _result("## 判定\n採用可")])
_LIMITED = "\n".join([
    _line(type="assistant", error="rate_limit", message={"content": [_text("You've hit your limit")]}),
    _line(type="result", is_error=True, api_error_status=429, result="You've hit your limit", session_id="S1"),
])


class ReviewCommandTests(unittest.TestCase):
    """CLI の起動（_run、preflight、capture_diff）を差し替えて review を走らせる。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.revs = []

    def tearDown(self):
        self._tmp.cleanup()

    def _review(self, *argv, diff="", outputs=(_OK,)):
        calls = []
        originals = ask._run, ask.preflight, ask.capture_diff

        def fake_run(cmd, prompt, cwd, timeout):
            calls.append((cmd, prompt))
            return 0, outputs[len(calls) - 1], ""

        def fake_diff(rev, cwd):
            self.revs.append(rev)
            return diff

        ask._run, ask.preflight, ask.capture_diff = fake_run, lambda: "claude", fake_diff
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    ask.main(["review", "--cwd", str(self.dir), *argv])
                    code = 0
                except SystemExit as e:
                    code = e.code
        finally:
            ask._run, ask.preflight, ask.capture_diff = originals
        return calls, code

    def test_targets_are_listed_and_review_is_read_only(self):
        calls, code = self._review("--target", "a.py")
        self.assertEqual(code, 0)
        cmd, prompt = calls[0]
        self.assertIn("- `a.py`", prompt)
        self.assertIn(ask.NO_INTENT, prompt)
        tools = cmd[cmd.index("--allowedTools") + 1:]
        self.assertIn("Read", tools)
        self.assertNotIn("Edit", tools)
        self.assertEqual(self.revs, [])

    def test_diff_is_embedded_and_recorded(self):
        diff = 'diff --git a/a.py b/a.py\n+y = "$HOME"\n'
        out = self.dir / "review.md"
        calls, code = self._review("--diff", "--task", "目的の説明", "--out", str(out), diff=diff)
        self.assertEqual(code, 0)
        prompt = calls[0][1]
        self.assertIn("```diff\n" + diff.rstrip() + "\n```", prompt)
        self.assertIn("`git diff HEAD`", prompt)
        self.assertIn("目的の説明", prompt)
        self.assertEqual(self.revs, ["HEAD"])
        self.assertEqual(Path(str(out) + ".target.diff").read_text(encoding="utf-8"), diff)
        st = ask.read_status(out)
        self.assertEqual((st["kind"], st["diff"], st["mode"], st["state"]),
                         ("review", "git diff HEAD", "analysis", "done"))

    def test_diff_takes_a_revision(self):
        out = self.dir / "review.md"
        calls, code = self._review("--diff", "main..HEAD", "--target", "a.py", "--out", str(out), diff="+1\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.revs, ["main..HEAD"])
        self.assertIn("- `a.py`", calls[0][1])
        self.assertIn("`git diff main..HEAD`", calls[0][1])

    def test_fails_before_calling_without_a_usable_target(self):
        for argv in ((), ("--target", "none.py"), ("--diff",)):
            calls, code = self._review(*argv, diff="")
            self.assertEqual(calls, [], argv)
            self.assertNotEqual(code, 0, argv)

    def test_review_falls_back_to_opus(self):
        out = self.dir / "review.md"
        calls, code = self._review("--target", "a.py", "--model", "fable", "--out", str(out),
                                   outputs=(_LIMITED, _OK))
        self.assertEqual(code, 0)
        models = [c[0][c[0].index("--model") + 1] for c in calls]
        self.assertEqual(models, ["claude-fable-5-1", "claude-opus-5-5"])
        self.assertEqual(calls[0][1], calls[1][1])
        st = ask.read_status(out)
        self.assertEqual((st["kind"], st["model"], st["fallback"]["from"]), ("review", "claude-opus-5-5", "claude-fable-5-1"))
    def test_detached_child_reviews_the_captured_diff(self):
        out = self.dir / "review.md"
        spawned = []
        original = ask._spawn_detached
        ask._spawn_detached = lambda subcmd, child, o, cwd, meta, started: spawned.append((subcmd, child, meta))
        try:
            calls, code = self._review("--diff", "--target", "a.py", "--task", "目的の説明", "--detach",
                                       "--out", str(out), diff="+1\n")
        finally:
            ask._spawn_detached = original
        self.assertEqual((code, calls), (0, []))
        subcmd, child, meta = spawned[0]
        self.assertEqual((subcmd, meta["kind"], meta["diff"]), ("review", "review", "git diff HEAD"))
        # 子は親が取り込んだ差分を読み、git diff を呼び直さない
        self.revs.clear()
        calls, code = self._review(*child)
        self.assertEqual(code, 0)
        self.assertEqual(self.revs, [])
        prompt = calls[0][1]
        for part in ("+1", "`git diff HEAD`", "- `a.py`", "目的の説明"):
            self.assertIn(part, prompt)

    def test_detach_creates_missing_output_directory(self):
        out = self.dir / "new" / "nested" / "review.md"
        started = []
        original = ask.subprocess.Popen

        class FakeProc:
            pid = 4321

        def fake_popen(cmd, **kw):
            started.append(cmd)
            return FakeProc()

        ask.subprocess.Popen = fake_popen
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ask._spawn_detached("review", ["--target", "a.py"], out, self.dir,
                                    {"label": "m", "effort": "high", "mode": "analysis"}, "t")
        finally:
            ask.subprocess.Popen = original
        self.assertEqual(started[0][2:4], ["review", "--target"])
        self.assertTrue(Path(str(out) + ".log").exists())
        self.assertEqual(ask.read_status(out)["pid"], 4321)



class BuildTargetTests(unittest.TestCase):
    def test_large_diff_points_to_the_file(self):
        text = ask.build_target([], "+" * (ask.DIFF_INLINE_LIMIT + 1), "git diff HEAD", Path("x.target.diff"))
        self.assertIn("x.target.diff", text)
        self.assertNotIn("```", text)

    def test_fence_outgrows_backticks_in_the_diff(self):
        text = ask.build_target([], "+```python\n+x\n+```\n", "git diff HEAD", None)
        self.assertIn("\n````diff\n", text)
        self.assertTrue(text.endswith("\n````"))


if __name__ == "__main__":
    unittest.main()
