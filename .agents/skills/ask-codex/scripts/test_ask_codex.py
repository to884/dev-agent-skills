"""`ask_codex.py` の取り込み規則、認証判定、モデル解決、テンプレート、review サブコマンドのテスト。CLI を起こさずに走る。

実行:
    python -m unittest discover -s .agents/skills/ask-codex/scripts -p "test_*.py"
"""

import contextlib
import importlib.util
import io
import json
import tempfile
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
        self.assertEqual(ask.resolve_model("Sol"), ("gpt-6.1-sol", "GPT-6.1 Sol"))
        self.assertEqual(ask.resolve_model("gpt-6.1-sol")[0], "gpt-6.1-sol")
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

    def test_review_prompt_has_all_headings(self):
        text = ask.build_prompt(ask.NO_INTENT, "analysis", ask.TEMPLATE_REVIEW, TARGET="- `a.py`")
        self.assertNotIn("$", text)
        for heading in REVIEW_HEADINGS:
            self.assertIn(f"`{heading}`", text)
        self.assertIn("崩せなかった", text)
        self.assertIn("書き換えない", text)


REVIEW_HEADINGS = ("## 判定", "## 致命的・重大な欠陥", "## 崩れる前提", "## 見落としと軽微な問題",
                   "## 検証した事実", "## 崩せなかった点")
_OK = "\n".join([_line(type="thread.started", thread_id="T1"),
                 _item("agent_message", text="## 判定\n採用可"),
                 _line(type="turn.completed", usage={})])


class AskCommandTests(unittest.TestCase):
    """review を足すために ask を組み替えたので、ask も main から通して確かめる。"""

    def _ask(self, *argv, out=None):
        calls = []
        originals = ask._run, ask.preflight

        def fake_run(cmd, prompt, cwd, timeout):
            calls.append((cmd, prompt))
            return 0, _OK, ""

        ask._run, ask.preflight = fake_run, lambda: "codex"
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                ask.main(["ask", "--task", "依頼本文", *argv])
        finally:
            ask._run, ask.preflight = originals
        return calls

    def test_mode_selects_the_sandbox(self):
        cmd, prompt = self._ask()[0]
        self.assertIn("依頼本文", prompt)
        self.assertIn("read-only", cmd)
        self.assertIn("書き換えない", prompt)
        cmd, prompt = self._ask("--mode", "edit")[0]
        self.assertIn("workspace-write", cmd)
        self.assertIn("コミットはしない", prompt)

    def test_status_records_kind_ask(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "answer.md"
            self._ask("--out", str(out))
            st = ask.read_status(out)
            self.assertEqual((st["kind"], st["mode"], st["state"]), ("ask", "analysis", "done"))
            self.assertEqual(out.read_text(encoding="utf-8"), "## 判定\n採用可\n")

    def test_detached_child_keeps_mode_and_task(self):
        spawned = []
        original = ask._spawn_detached
        ask._spawn_detached = lambda subcmd, child, o, cwd, meta, started: spawned.append((subcmd, child))
        try:
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "answer.md"
                self._ask("--mode", "edit", "--detach", "--out", str(out))
                subcmd, child = spawned[0]
                self.assertEqual(subcmd, "ask")
                self.assertEqual(child[child.index("--mode") + 1], "edit")
                self.assertEqual(Path(child[child.index("--task-file") + 1]).read_text(encoding="utf-8"), "依頼本文")
        finally:
            ask._spawn_detached = original


class ReviewCommandTests(unittest.TestCase):
    """CLI の起動（_run、preflight、capture_diff）を差し替えて review を走らせる。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.revs = []

    def tearDown(self):
        self._tmp.cleanup()

    def _review(self, *argv, diff=""):
        calls = []
        originals = ask._run, ask.preflight, ask.capture_diff

        def fake_run(cmd, prompt, cwd, timeout):
            calls.append((cmd, prompt))
            return 0, _OK, ""

        def fake_diff(rev, cwd):
            self.revs.append(rev)
            return diff

        ask._run, ask.preflight, ask.capture_diff = fake_run, lambda: "codex", fake_diff
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
        self.assertIn("read-only", cmd)
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
