#!/usr/bin/env python3
"""ask-claude: Codex から Claude Code CLI の Claude Fable 5.1 または Claude Opus 5.5 に 1 つのタスクを依頼する。

Claude の最終メッセージだけを標準出力（と --out のファイル）に返す。進行のログは標準エラーに出す。
長い依頼は --detach で切り離して起動し、wait で受け取る。
Fable 5.1 が利用制限で使えなければ、Opus 5.5 で同じ依頼をやり直す（--no-fallback で止める）。

使い方:
  python ask_claude.py check
  python ask_claude.py ask (--task <text> | --task-file <path>) [--model fable|opus] [--effort medium|high|xhigh]
                       [--mode analysis|edit] [--allow "Bash(make:*)"] [--out <file>] [--detach]
                       [--resume <session-id>] [--cwd <dir>] [--timeout 1800] [--no-fallback]
  python ask_claude.py wait <out> [--timeout 570]
  python ask_claude.py status <out>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

NAME = "ask-claude"
SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "templates" / "task.md"

MODELS = {"fable": ("claude-fable-5-1", "Claude Fable 5.1"), "opus": ("claude-opus-5-5", "Claude Opus 5.5")}
DEFAULT_MODEL = "fable"
# 利用制限で使えないときに切り替える先。キーはモデル ID、値は MODELS のキー。
FALLBACK = {MODELS["fable"][0]: "opus"}
CONTINUE_PROMPT = (
    "利用制限のため、前のモデルの作業がここで止まった。ここまでの作業を引き継ぎ、最初の依頼を最後まで進めて、"
    "最終メッセージを書く。最初の依頼の制約と出力形式に従う。"
)
EFFORTS = ("medium", "high", "xhigh")
DEFAULT_EFFORT = "high"
PERMISSION = {"analysis": "auto", "edit": "auto"}
TOOLS_ANALYSIS = [
    "Read", "Glob", "Grep", "WebFetch", "WebSearch",
    "Bash(git log:*)", "Bash(git show:*)", "Bash(git diff:*)",
    "Bash(git blame:*)", "Bash(git status:*)", "Bash(git ls-files:*)",
]
TOOLS_EDIT = TOOLS_ANALYSIS + ["Edit", "Write", "MultiEdit", "NotebookEdit", "Bash(git:*)"]


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[{NAME} {dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", newline="\n")


def child_env() -> dict:
    """子プロセスの環境。Claude Code のセッション内で引き継がれる `CLAUDE*` を外さないと、
    子の `claude -p` が入れ子として扱われて動かない。"""
    env = dict(os.environ)
    keep = {"CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CONFIG_DIR"}
    for k in list(env):
        if k.startswith("CLAUDE") and k not in keep:
            env.pop(k)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def find_claude() -> str:
    p = os.environ.get("ASK_CLAUDE_EXE") or shutil.which("claude")
    if p:
        return p
    for cand in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude"):
        if cand.exists():
            return str(cand)
    raise SystemExit("claude CLI が見つかりません。環境変数 ASK_CLAUDE_EXE で実行ファイルを指定してください。")


def resolve_model(name: str) -> tuple[str, str]:
    key = name.strip().lower()
    if key in MODELS:
        return MODELS[key]
    if "-" in key:  # モデル ID をそのまま渡した
        return key, key
    raise SystemExit(f"未知のモデル: {name}（{'、'.join(MODELS)} かモデル ID を指定する）")


# ---------------------------------------------------------------------------
# CLI の確認
# ---------------------------------------------------------------------------

def _quiet_run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=child_env(), timeout=timeout)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def parse_claude_auth(stdout: str) -> tuple[bool, str]:
    """`claude auth status --json` の出力からログイン状態を読む。"""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return False, "auth status を読めません。`claude auth login` を実行してください"
    if data.get("loggedIn"):
        return True, f"ログイン済み（{data.get('authMethod') or '?'}）"
    return False, "未ログイン。`claude auth login` を実行してください"


API_HOSTS = ("api.anthropic.com",)
NET_HINT = ("Codex のサンドボックス内なら、config.toml で sandbox_workspace_write.network_access = true を"
            "設定するか、サンドボックス外での実行を承認する")


def _proxy_target(env: dict | None = None) -> tuple[str, int] | None:
    """HTTPS_PROXY が設定されていれば、その host と port を返す。"""
    env = os.environ if env is None else env
    url = env.get("HTTPS_PROXY") or env.get("https_proxy") or ""
    if not url:
        return None
    rest = url.split("://", 1)[-1].rstrip("/").rsplit("@", 1)[-1]
    host, _, port = rest.partition(":")
    return host, (int(port) if port.isdigit() else 443)


def network_ok(hosts: tuple[str, ...] = API_HOSTS) -> tuple[bool, str]:
    """API のホスト（プロキシがあればプロキシ）へ TCP 接続できるかを 5 秒で確かめる。

    Codex のサンドボックスは既定でネットワークを遮断し、`claude` は接続拒否のまま数分待ってから
    落ちる（2026-09-24 実測）。先に見つけて止める。
    """
    proxy = _proxy_target()
    targets = [proxy] if proxy else [(h, 443) for h in hosts]
    errors = []
    for host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=5):
                return True, f"到達できる（{host}:{port}）"
        except OSError as e:
            errors.append(f"{host}:{port} {e}")
    return False, "到達できない（" + "; ".join(errors) + "）。" + NET_HINT


def check_claude() -> dict:
    info = {"exe": "", "version": "", "auth": "", "net": "", "ok": False}
    try:
        exe = find_claude()
    except SystemExit as e:
        info["auth"] = str(e)
        return info
    info["exe"] = exe
    try:
        info["version"] = _first_line(_quiet_run([exe, "--version"]).stdout) or "?"
        proc = _quiet_run([exe, "auth", "status", "--json"])
        auth_ok, info["auth"] = parse_claude_auth(proc.stdout)
    except (OSError, subprocess.TimeoutExpired) as e:
        auth_ok, info["auth"] = False, f"実行できません（{e}）"
    net_ok, info["net"] = network_ok()
    info["ok"] = auth_ok and net_ok
    return info


def preflight() -> str:
    info = check_claude()
    if not info["ok"]:
        raise SystemExit(f"claude: {info['auth']} / ネットワーク: {info['net'] or '未確認'}")
    return info["exe"]


def cmd_check(args: argparse.Namespace) -> None:
    r = check_claude()
    print("| CLI | 実行ファイル | バージョン | 認証 | ネットワーク |")
    print("| --- | --- | --- | --- | --- |")
    print(f"| claude | {r['exe'] or '見つかりません'} | {r['version'] or '-'} | {r['auth']} | {r['net'] or '-'} |")
    if not r["ok"]:
        raise SystemExit("claude CLI が使えません。上の表の項目を直してからやり直してください。")
    print("claude CLI が使えます。")


# ---------------------------------------------------------------------------
# CLI 呼び出し
# ---------------------------------------------------------------------------

class CallResult:
    def __init__(self) -> None:
        self.ok = False
        self.text = ""
        self.session_id: str | None = None
        self.error = ""
        self.stdout = ""
        self.stderr = ""
        self.usage: dict = {}
        self.returncode: int | None = None
        self.rate_limited = False
        self.used_tools = False


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        subprocess.run(["kill", "-9", str(pid)], capture_output=True)


def _run(cmd: list[str], prompt: str, cwd: Path, timeout: int) -> tuple[int | None, str, str]:
    """CLI を起動し、プロンプトを標準入力で渡し、標準出力と標準エラーを集めて返す。

    プロンプトを引数にしないのは、`--allowedTools` が可変長引数で、後ろの位置引数を飲むためである。
    """
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", cwd=str(cwd), env=child_env(),
    )
    out_lines: list[str] = []
    err_lines: list[str] = []

    def pump(stream, sink: list[str]) -> None:
        for line in iter(stream.readline, ""):
            sink.append(line)

    readers = [
        threading.Thread(target=pump, args=(proc.stdout, out_lines), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, err_lines), daemon=True),
    ]
    for t in readers:
        t.start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    timed_out = False
    try:
        rc: int | None = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        rc = None
    for t in readers:
        t.join(timeout=10)
    err = "".join(err_lines)
    if timed_out:
        err += f"\n[{NAME}] timeout after {timeout}s\n"
    return rc, "".join(out_lines), err


def _parse_claude_stream(stdout: str) -> tuple[dict | None, str]:
    """stream-json の行を読み、最終イベントと最終回答の本文を返す。

    最終回答は「最後のツール呼び出しより後に出たアシスタントのテキスト」をすべて連結したもの。
    `--output-format json` の result は最後の 1 メッセージしか運ばないので使わない。
    """
    final = None
    texts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "assistant":
            for block in ev.get("message", {}).get("content") or []:
                btype = block.get("type")
                if btype == "tool_use":
                    texts.clear()
                elif btype == "text" and block.get("text"):
                    texts.append(block["text"])
        elif kind == "result":
            final = ev
    if final is None:
        return None, ""
    joined = "".join(texts).strip()
    fallback = (final.get("result") or "").strip()
    return final, joined if len(joined) >= len(fallback) else fallback


def _stream_signals(stdout: str) -> tuple[bool, bool]:
    """stream-json の行から（利用制限で止まったか、ツールを呼んだか）を返す。

    利用制限（429）では、`claude` はアシスタントのメッセージに `"error": "rate_limit"` を付け、
    最終イベントに `api_error_status: 429` を入れる。`--fallback-model` は 429 では切り替わらない
    （Claude Code 2.1.289 で確認）ので、ここで見つけて呼び出し側で切り替える。
    """
    limited = used = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "assistant":
            if ev.get("error") == "rate_limit":
                limited = True
            if any(b.get("type") == "tool_use" for b in ev.get("message", {}).get("content") or []):
                used = True
        elif kind == "result" and ev.get("is_error") and ev.get("api_error_status") == 429:
            limited = True
    return limited, used


def call_claude(exe: str, model: str, effort: str, prompt: str, cwd: Path, timeout: int,
                permission_mode: str, allowed_tools: list[str], resume: str | None = None) -> CallResult:
    r = CallResult()
    cmd = [exe, "-p", "--output-format", "stream-json", "--verbose",
           "--model", model, "--effort", effort, "--permission-mode", permission_mode]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    if resume:
        cmd += ["--resume", resume]
    r.returncode, r.stdout, r.stderr = _run(cmd, prompt, cwd, timeout)
    if r.returncode is None:
        r.error = "timeout"
        return r
    r.rate_limited, r.used_tools = _stream_signals(r.stdout)
    final, text = _parse_claude_stream(r.stdout)
    if final is None:
        r.error = f"JSON を読めません（returncode={r.returncode}）: {_first_line(r.stderr)[:200]}"
        return r
    r.session_id = final.get("session_id")
    r.usage = {"total_cost_usd": final.get("total_cost_usd"), "usage": final.get("usage")}
    r.text = text
    if final.get("is_error") or not r.text.strip():
        r.error = f"claude が失敗しました: {r.text[:300]}"
        return r
    r.ok = True
    return r


# ---------------------------------------------------------------------------
# プロンプトと状態ファイル
# ---------------------------------------------------------------------------

def mode_note(mode: str) -> str:
    if mode == "edit":
        return (
            "- 作業ツリーを直接編集してよい。コミットはしない。\n"
            "- ビルドやテストができれば実行し、結果の要点を書く。変更したファイルの一覧を最終メッセージに含める。"
        )
    return "- 調査と判断の依頼である。リポジトリのファイルを書き換えない。読むための道具は使える。"


def build_prompt(task: str, mode: str) -> str:
    tpl = string.Template(read_text(TEMPLATE))
    return tpl.safe_substitute(TASK=task.strip(), MODE_NOTE=mode_note(mode))


def status_path(out: Path) -> Path:
    return Path(str(out) + ".status.json")


def write_status(target: Path, **fields) -> None:
    write_text(status_path(target), json.dumps(fields, ensure_ascii=False, indent=2) + "\n")


def read_status(out: Path) -> dict | None:
    p = status_path(out)
    if not p.exists():
        return None
    return json.loads(read_text(p))


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        proc = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in proc.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------

def _read_task(args: argparse.Namespace) -> str:
    if args.task_file:
        return read_text(Path(args.task_file))
    if args.task:
        return args.task
    raise SystemExit("--task か --task-file のどちらかを指定してください。")


def cmd_ask(args: argparse.Namespace) -> None:
    model_id, label = resolve_model(args.model)
    task = _read_task(args)
    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd()
    out = Path(args.out).resolve() if args.out else None
    started = dt.datetime.now().isoformat(timespec="seconds")
    meta = {"model": model_id, "label": label, "effort": args.effort, "mode": args.mode, "cwd": str(cwd)}

    if args.detach:
        if out is None:
            out = Path(tempfile.gettempdir()) / NAME / f"{dt.datetime.now():%Y%m%d-%H%M%S}.md"
        task_path = Path(str(out) + ".task.md")
        write_text(task_path, task)
        child = [sys.executable, str(Path(__file__).resolve()), "ask", "--task-file", str(task_path),
                 "--model", model_id, "--effort", args.effort, "--mode", args.mode,
                 "--out", str(out), "--cwd", str(cwd), "--timeout", str(args.timeout)]
        if args.allow:
            child += ["--allow", args.allow]
        if args.resume:
            child += ["--resume", args.resume]
        if args.no_fallback:
            child.append("--no-fallback")
        logf = open(str(out) + ".log", "w", encoding="utf-8")
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(child, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                cwd=str(cwd), env=child_env(), close_fds=True, **kwargs)
        write_status(out, state="running", pid=proc.pid, started=started, out=str(out), **meta)
        script = Path(__file__).resolve()
        print(f"[{NAME}] 切り離して起動しました。pid {proc.pid}、{label}、effort {args.effort}、{args.mode}")
        print(f"[{NAME}] 結果: {out}")
        print(f"[{NAME}] 受け取る: python \"{script}\" wait \"{out}\"")
        return

    exe = preflight()
    prompt = build_prompt(task, args.mode)
    tools = list(TOOLS_EDIT if args.mode == "edit" else TOOLS_ANALYSIS)
    if args.allow:
        tools += [t.strip() for t in args.allow.split(",") if t.strip()]
    if out is not None:
        write_status(out, state="running", pid=os.getpid(), started=started, out=str(out), **meta)
    log(f"{label} に依頼（effort {args.effort}、{args.mode}、cwd {cwd}）")
    res = call_claude(exe, model_id, args.effort, prompt, cwd, args.timeout, PERMISSION[args.mode], tools, args.resume)
    fallback = None
    if not res.ok and res.rate_limited and not args.no_fallback and model_id in FALLBACK:
        fb_id, fb_label = MODELS[FALLBACK[model_id]]
        # ツールを呼んだ後で止まったなら、そのセッションを引き継いで続けさせる。何もしていなければ最初からやり直す。
        carry = res.used_tools and res.session_id
        resume, fb_prompt = (res.session_id, CONTINUE_PROMPT) if carry else (args.resume, prompt)
        fallback = {"from": model_id, "error": res.error, "session_id": res.session_id, "usage": res.usage}
        log(f"{label} が利用制限で使えません。{fb_label} に切り替えて{'続けます' if carry else 'やり直します'}")
        model_id, label = fb_id, fb_label
        meta.update(model=model_id, label=label)
        if out is not None:
            write_status(out, state="running", pid=os.getpid(), started=started, out=str(out), fallback=fallback, **meta)
        res = call_claude(exe, model_id, args.effort, fb_prompt, cwd, args.timeout, PERMISSION[args.mode], tools, resume)
    finished = dt.datetime.now().isoformat(timespec="seconds")
    if out is not None:
        if res.ok:
            write_text(out, res.text.rstrip() + "\n")
        else:
            write_text(Path(str(out) + ".error.log"), res.stdout + "\n--- stderr ---\n" + res.stderr)
        write_status(out, state="done" if res.ok else "failed", pid=os.getpid(), started=started,
                     finished=finished, out=str(out), session_id=res.session_id, usage=res.usage,
                     error=res.error, fallback=fallback, **meta)
    if not res.ok:
        log(f"失敗: {res.error}")
        if out is None:
            sys.stderr.write(res.stderr[-2000:])
        raise SystemExit(1)
    sys.stdout.write(res.text.rstrip() + "\n")
    sys.stdout.flush()
    log(f"完了（{label}）。session {res.session_id}  usage {json.dumps(res.usage, ensure_ascii=False)}")


def cmd_wait(args: argparse.Namespace) -> None:
    out = Path(args.out).resolve()
    deadline = time.time() + args.timeout
    while True:
        st = read_status(out)
        if st is None:
            raise SystemExit(f"状態ファイルがありません: {status_path(out)}")
        if st["state"] != "running":
            break
        pid = st.get("pid")
        if pid and not _pid_alive(int(pid)):
            raise SystemExit(f"実行プロセス（pid {pid}）が結果を書かずに終わりました。{out}.log を見てください。")
        if time.time() >= deadline:
            print(f"[{NAME}] まだ実行中です（pid {pid}、開始 {st.get('started')}）。もう一度 wait を実行してください。",
                  file=sys.stderr)
            raise SystemExit(3)
        time.sleep(5)
    if st["state"] == "done":
        sys.stdout.write(read_text(out))
        sys.stdout.flush()
        log(f"完了（{st.get('label')}）。session {st.get('session_id')}  usage {json.dumps(st.get('usage'), ensure_ascii=False)}")
        return
    raise SystemExit(f"失敗しました: {st.get('error')}。詳細は {out}.log と {out}.error.log を見てください。")


def cmd_status(args: argparse.Namespace) -> None:
    st = read_status(Path(args.out).resolve())
    if st is None:
        raise SystemExit("状態ファイルがありません。")
    print(json.dumps(st, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="claude の実行ファイル、バージョン、ログイン状態を確かめる")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("ask", help="Claude に依頼し、最終メッセージを受け取る")
    p.add_argument("--task", help="依頼の本文")
    p.add_argument("--task-file", help="依頼の本文を書いたファイル")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"{'、'.join(MODELS)} かモデル ID（既定 {DEFAULT_MODEL}）")
    p.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT, help=f"既定 {DEFAULT_EFFORT}")
    p.add_argument("--mode", choices=list(PERMISSION), default="analysis", help="analysis は読み取り専用、edit は作業ツリーを編集できる")
    p.add_argument("--allow", help="edit モードで追加で許す道具（例: \"Bash(make:*),Bash(python:*)\"）")
    p.add_argument("--out", help="最終メッセージを書くファイル")
    p.add_argument("--detach", action="store_true", help="切り離して起動し、wait で受け取る")
    p.add_argument("--resume", help="続きを聞くセッション ID（同じ作業ディレクトリから呼ぶ）")
    p.add_argument("--cwd", help="Claude の作業ディレクトリ（既定は現在のディレクトリ）")
    p.add_argument("--timeout", type=int, default=1800, help="呼び出しの上限秒数（既定 1800）")
    p.add_argument("--no-fallback", action="store_true", help="Fable 5.1 が利用制限で使えなくても Opus 5.5 に切り替えない")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("wait", help="切り離した依頼の完了を待ち、最終メッセージを出す")
    p.add_argument("out")
    p.add_argument("--timeout", type=int, default=570, help="待つ上限秒数（既定 570）。超えたら終了コード 3")
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("status", help="切り離した依頼の状態を出す")
    p.add_argument("out")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
