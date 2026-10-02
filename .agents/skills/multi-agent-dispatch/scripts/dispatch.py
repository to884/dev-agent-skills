#!/usr/bin/env python3
"""multi-agent-dispatch のオーケストレーター。

同じタスクを最大 4 体のエージェント（Claude Opus 5.5、Claude Fable 5.1、GPT-6.1 Sol、GPT-6 Astra）に
`claude -p` と `codex exec` で独立に解かせ、成果を集める。任意で相互レビューと総括を続ける。

各 CLI の最終メッセージは標準出力の JSON ストリームから組み立て、全エージェントが終わってから
初めてファイルへ書く。途中で他のエージェントの成果がリポジトリに現れないようにするためである。

使い方:
  python dispatch.py check
  python dispatch.py init --slug <slug> (--task-file <path> | --task <text>)
                     [--agents A,B,C,D] [--effort medium|high|xhigh] [--mode analysis|implement]
  python dispatch.py run <run-dir> [--only A,B]
  python dispatch.py review <run-dir> [--only A,B]
  python dispatch.py synthesize <run-dir>
  python dispatch.py all <run-dir>
  python dispatch.py status <run-dir>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import string
import subprocess
import sys
import threading
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = SKILL_DIR / "templates"

# エージェントの既定。run.json へ写してから使うので、実行ごとに書き換えられる。
DEFAULT_AGENTS = {
    "A": {"vendor": "claude", "model": "claude-opus-5-5", "label": "Claude Opus 5.5"},
    "B": {"vendor": "claude", "model": "claude-fable-5-1", "label": "Claude Fable 5.1"},
    "C": {"vendor": "codex", "model": "gpt-6.1-sol", "label": "GPT-6.1 Sol"},
    "D": {"vendor": "codex", "model": "gpt-6-astra", "label": "GPT-6 Astra"},
}
EFFORTS = ("medium", "high", "xhigh")
DEFAULT_EFFORT = "high"
SYNTHESIS_EFFORT = "medium"
DEFAULT_SYNTH = {"vendor": "claude", "model": "claude-fable-5-1", "label": "Claude Fable 5.1"}
DEFAULT_OUT_DIR = "docs/agent-runs"
WORKTREE_DIR = ".agents/worktrees"

# analysis モードで Claude に許す道具。読み取り専用に絞る。
CLAUDE_TOOLS_ANALYSIS = [
    "Read", "Glob", "Grep", "WebFetch", "WebSearch",
    "Bash(git log:*)", "Bash(git show:*)", "Bash(git diff:*)",
    "Bash(git blame:*)", "Bash(git status:*)", "Bash(git ls-files:*)",
]
# implement モードでは編集と git を許す。ビルドやテストのコマンドは init の --allow で足す。
CLAUDE_TOOLS_IMPLEMENT = CLAUDE_TOOLS_ANALYSIS + [
    "Edit", "Write", "MultiEdit", "NotebookEdit", "Bash(git:*)",
]

PATCH_INLINE_LIMIT = 200_000  # レビューへ差分を貼り込む上限（バイト）。超えたらファイルを指す。


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    ts = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[dispatch {ts}] {msg}", flush=True)


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Path(out)


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", newline="\n")


def load_run(run_dir: Path) -> dict:
    return json.loads(read_text(run_dir / "run.json"))


def save_run(run_dir: Path, run: dict) -> None:
    write_text(run_dir / "run.json", json.dumps(run, ensure_ascii=False, indent=2) + "\n")


def render(template_name: str, **vars: str) -> str:
    tpl = string.Template(read_text(TEMPLATE_DIR / template_name))
    return tpl.safe_substitute(**vars)


def agent_vars(run: dict) -> dict[str, str]:
    """エージェントの数と一覧。テンプレートに「4 体」を直書きせず、実際の数に合わせる。"""
    keys = list(run["agents"])
    return {
        "N_AGENTS": str(len(keys)),
        "N_OTHERS": str(len(keys) - 1),
        "AGENT_LIST": "、".join(f"エージェント {k}" for k in keys),
    }


def child_env() -> dict:
    """子プロセスの環境。

    Claude Code のセッション内から呼ばれたとき、親の `CLAUDE*` 系の変数を引き継ぐと
    子の `claude -p` が入れ子として扱われて動かない。Git Bash の場所と設定ディレクトリは残す。
    """
    env = dict(os.environ)
    keep = {"CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CONFIG_DIR"}
    for k in list(env):
        if k.startswith("CLAUDE") and k not in keep:
            env.pop(k)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def find_claude() -> str:
    p = os.environ.get("DISPATCH_CLAUDE") or shutil.which("claude")
    if p:
        return p
    for cand in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude"):
        if cand.exists():
            return str(cand)
    raise SystemExit("claude CLI が見つかりません。環境変数 DISPATCH_CLAUDE で実行ファイルを指定してください。")


def find_codex() -> str:
    p = os.environ.get("DISPATCH_CODEX") or shutil.which("codex")
    if p:
        return p
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    cands = sorted(base.glob("*/codex.exe"), key=lambda x: x.stat().st_mtime)
    if cands:
        return str(cands[-1])
    raise SystemExit("codex CLI が見つかりません。環境変数 DISPATCH_CODEX で実行ファイルを指定してください。")


# ---------------------------------------------------------------------------
# CLI の確認（check、各段階の前）
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


def parse_codex_auth(returncode: int | None, output: str) -> tuple[bool, str]:
    """`codex login status` の出力からログイン状態を読む。

    ログイン済みなら終了コード 0 で `Logged in using ChatGPT` のような 1 行を出す。
    """
    line = next((ln.strip() for ln in output.splitlines() if "logged in" in ln.lower()), "")
    if returncode == 0 and line and not line.lower().startswith("not "):
        return True, line
    return False, (line or _first_line(output) or "未ログイン") + "。`codex login` を実行してください"


def check_vendor(vendor: str) -> dict:
    """CLI の実行ファイル、バージョン、ログイン状態を調べる。"""
    info = {"vendor": vendor, "exe": "", "version": "", "auth": "", "ok": False}
    try:
        exe = find_claude() if vendor == "claude" else find_codex()
    except SystemExit as e:
        info["auth"] = str(e)
        return info
    info["exe"] = exe
    try:
        info["version"] = _first_line(_quiet_run([exe, "--version"]).stdout) or "?"
        if vendor == "claude":
            proc = _quiet_run([exe, "auth", "status", "--json"])
            info["ok"], info["auth"] = parse_claude_auth(proc.stdout)
        else:
            proc = _quiet_run([exe, "login", "status"])
            info["ok"], info["auth"] = parse_codex_auth(proc.returncode, proc.stdout + proc.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        info["auth"] = f"実行できません（{e}）"
    return info


def preflight(vendors: set[str]) -> None:
    """走らせる前に、使う CLI が実行でき、ログイン済みであることを確かめる。"""
    bad = [i for i in (check_vendor(v) for v in sorted(vendors)) if not i["ok"]]
    if bad:
        raise SystemExit("\n".join(f"{i['vendor']}: {i['auth']}" for i in bad))


def cmd_check(args: argparse.Namespace) -> None:
    rows = [check_vendor(v) for v in ("claude", "codex")]
    print("| CLI | 実行ファイル | バージョン | 認証 |")
    print("| --- | --- | --- | --- |")
    for r in rows:
        print(f"| {r['vendor']} | {r['exe'] or '見つかりません'} | {r['version'] or '-'} | {r['auth']} |")
    if not all(r["ok"] for r in rows):
        raise SystemExit("使えない CLI があります。上の表の項目を直してからやり直してください。")
    print("両方の CLI が使えます。")


# ---------------------------------------------------------------------------
# CLI 呼び出し
# ---------------------------------------------------------------------------

class CallResult:
    def __init__(self) -> None:
        self.ok = False
        self.text = ""
        self.session_id: str | None = None
        self.resumed = False
        self.error = ""
        self.stdout = ""
        self.stderr = ""
        self.usage: dict = {}
        self.returncode: int | None = None


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        subprocess.run(["kill", "-9", str(pid)], capture_output=True)


def _reap_children(pid: int) -> None:
    """本体が終わったあとに残った子プロセスを止める。

    Windows の Codex は `codex-windows-sandbox-setup.exe` を起動し、これが本体の終了後も
    標準出力のパイプを握ったまま残ることがある。残すと読む側が永久に待つ。
    """
    if os.name != "nt":
        return
    query = (
        f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}' | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", query], capture_output=True)


def _run(cmd: list[str], prompt: str, cwd: Path, timeout: int) -> tuple[int | None, str, str]:
    """CLI を起動し、プロンプトを標準入力で渡し、標準出力と標準エラーを集めて返す。

    `subprocess.run` はパイプが閉じるまで戻らないので使わない。本体プロセスの終了を待ち、
    パイプを握って残った子は回収する。
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
    _reap_children(proc.pid)
    for t in readers:
        t.join(timeout=10)
    err = "".join(err_lines)
    if timed_out:
        err += f"\n[dispatch] timeout after {timeout}s\n"
    return rc, "".join(out_lines), err


def call_claude(model: str, effort: str, prompt: str, cwd: Path, timeout: int,
                permission_mode: str, allowed_tools: list[str],
                resume: str | None = None) -> CallResult:
    r = CallResult()
    exe = find_claude()
    # stream-json を使う理由: --output-format json の result は「最後の 1 メッセージ」しか
    # 運ばない。長い成果をモデルが複数のメッセージへ分けて出すと末尾しか残らない。
    # stream-json なら全メッセージが 1 行ずつ流れるので、最終回答を組み直せる。
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
    final, text = _parse_claude_stream(r.stdout)
    if final is None:
        r.error = f"JSON を読めません（returncode={r.returncode}）: {_first_line(r.stderr)[:200]}"
        return r
    r.session_id = final.get("session_id")
    r.usage = {"total_cost_usd": final.get("total_cost_usd"), "usage": final.get("usage")}
    r.text = text
    r.resumed = bool(resume)
    if final.get("is_error") or not r.text.strip():
        r.error = f"claude が失敗しました: {r.text[:300]}"
        return r
    r.ok = True
    return r


def _parse_claude_stream(stdout: str) -> tuple[dict | None, str]:
    """stream-json の行を読み、最終イベントと最終回答の本文を返す。

    最終回答は「最後のツール呼び出しより後に出たアシスタントのテキスト」をすべて連結した
    ものである。調査中のナレーションはツール呼び出しの合間に挟まるので、この規則で落ちる。
    連結に区切り文字を入れないのは、文や式の途中で分かれることがあるためである。
    """
    final = None
    texts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue  # 警告行など、JSON でない行は読み飛ばす
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "assistant":
            for block in ev.get("message", {}).get("content") or []:
                btype = block.get("type")
                if btype == "tool_use":
                    texts.clear()  # ここまでのテキストは調査中のナレーションだった
                elif btype == "text" and block.get("text"):
                    texts.append(block["text"])
        elif kind == "result":
            final = ev
    if final is None:
        return None, ""
    joined = "".join(texts).strip()
    fallback = (final.get("result") or "").strip()
    return final, joined if len(joined) >= len(fallback) else fallback


def call_codex(model: str, effort: str, prompt: str, cwd: Path, timeout: int,
               sandbox: str, resume: str | None = None) -> CallResult:
    r = CallResult()
    exe = find_codex()
    common = ["-c", f'model_reasoning_effort="{effort}"', "-c", 'approval_policy="never"', "--json"]
    if resume:
        # exec resume は -s / -C を持たず、元のセッションの設定を引き継ぐ。
        cmd = [exe, "exec", "resume", *common, "-m", model, resume, "-"]
    else:
        cmd = [exe, "exec", *common, "-m", model, "-s", sandbox, "-C", str(cwd), "-"]
    r.returncode, r.stdout, r.stderr = _run(cmd, prompt, cwd, timeout)
    if r.returncode is None:
        r.error = "timeout"
        return r
    # 最終回答は「最後のツール実行より後に出た agent_message」の連なり。
    # agent_message と reasoning 以外の項目が来たら、それまでの分を捨てる。
    msgs: list[str] = []
    last_msg = ""
    errors: list[str] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "thread.started":
            r.session_id = ev.get("thread_id")
        elif t == "item.completed":
            item = ev.get("item") or {}
            itype = item.get("type")
            if itype == "agent_message":
                text = item.get("text") or ""
                if text:
                    msgs.append(text)
                    last_msg = text
            elif itype == "error":
                errors.append(item.get("message") or "")
                msgs.clear()
            elif itype != "reasoning":
                msgs.clear()
        elif t == "turn.completed":
            r.usage = ev.get("usage") or {}
        elif t == "turn.failed":
            errors.append(json.dumps(ev.get("error"), ensure_ascii=False))
    joined = "".join(msgs).strip()
    r.text = joined if joined else last_msg
    r.resumed = bool(resume)
    if not last_msg.strip():
        r.error = "codex の最終メッセージが空です: " + (" / ".join(errors) or _first_line(r.stderr))[:300]
        return r
    r.ok = True
    return r


def call_agent(run: dict, agent: dict, phase: str, prompt: str, cwd: Path,
               resume: str | None) -> CallResult:
    effort = agent.get("effort", {}).get(phase) or run["effort"][phase]
    timeout = int(run.get("timeout_s", 3600))
    if agent["vendor"] == "claude":
        mode = run["mode"]
        pm = run["claude"]["permission_mode"][mode]
        tools = run["claude"]["allowed_tools"][mode]
        return call_claude(agent["model"], effort, prompt, cwd, timeout, pm, tools, resume)
    if agent["vendor"] == "codex":
        sandbox = run["codex"]["sandbox"][run["mode"]]
        return call_codex(agent["model"], effort, prompt, cwd, timeout, sandbox, resume)
    raise ValueError(f"未知のベンダー: {agent['vendor']}")


# ---------------------------------------------------------------------------
# worktree（implement モード）
# ---------------------------------------------------------------------------

def ensure_worktree(root: Path, run: dict, key: str) -> Path:
    agent = run["agents"][key]
    base = root / WORKTREE_DIR
    wt = base / f"{run['run_id']}-{key}"
    if wt.exists():
        return wt
    ignore = base / ".gitignore"
    if not ignore.exists():
        write_text(ignore, "*\n")  # worktree を親リポジトリの未追跡一覧に出さない
    branch = f"dispatch/{run['run_id']}/{key}"
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(wt), run["base_commit"]],
        cwd=str(root), check=True, capture_output=True, text=True,
    )
    agent["worktree"] = str(wt)
    agent["branch"] = branch
    return wt


def capture_patch(wt: Path) -> str:
    subprocess.run(["git", "add", "-A"], cwd=str(wt), check=True, capture_output=True)
    patch = subprocess.run(
        ["git", "diff", "--cached", "--binary"], cwd=str(wt), check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    subprocess.run(["git", "reset", "-q"], cwd=str(wt), check=True, capture_output=True)
    return patch


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    root = repo_root()
    if args.task_file:
        task = read_text(Path(args.task_file))
    elif args.task:
        task = args.task
    else:
        raise SystemExit("--task-file か --task のどちらかを指定してください。")
    slug = re.sub(r"[^a-z0-9-]+", "-", args.slug.lower()).strip("-")
    run_id = f"{dt.date.today().isoformat()}-{slug}"
    run_dir = root / args.out_dir / run_id
    if run_dir.exists():
        raise SystemExit(f"{run_dir} はすでにあります。")
    agents = {k: dict(v) for k, v in DEFAULT_AGENTS.items()}
    if args.agents:
        keys = [k.strip().upper() for k in args.agents.split(",") if k.strip()]
        unknown = [k for k in keys if k not in agents]
        if unknown:
            raise SystemExit(f"未知のエージェント: {'、'.join(unknown)}（A〜D から選ぶ）")
        agents = {k: agents[k] for k in keys}
    for a in agents.values():
        a["status"] = {}
        a["session_id"] = None
    synth = dict(DEFAULT_SYNTH)
    if args.synth_model:
        synth["model"] = args.synth_model
        synth["vendor"] = "codex" if args.synth_model.startswith("gpt") else "claude"
        synth["label"] = args.synth_model
    preflight({a["vendor"] for a in agents.values()})
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True,
                          text=True, check=True).stdout.strip()
    implement_tools = list(CLAUDE_TOOLS_IMPLEMENT)
    if args.allow:
        implement_tools += [t.strip() for t in args.allow.split(",") if t.strip()]
    run = {
        "run_id": run_id,
        "slug": slug,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "base_commit": base,
        "timeout_s": args.timeout,
        "effort": {"run": args.effort, "review": args.effort, "synthesis": SYNTHESIS_EFFORT},
        "agents": agents,
        "synthesis": synth,
        "claude": {
            "permission_mode": {"analysis": "dontAsk", "implement": "acceptEdits"},
            "allowed_tools": {"analysis": CLAUDE_TOOLS_ANALYSIS, "implement": implement_tools},
        },
        "codex": {"sandbox": {"analysis": "read-only", "implement": "workspace-write"}},
        "usage": {},
    }
    write_text(run_dir / "task.md", task.rstrip() + "\n")
    write_text(run_dir / "logs" / ".gitignore", "*\n")  # 生出力はコミットしない
    save_run(run_dir, run)
    log(f"初期化しました: {run_dir.relative_to(root)}（エージェント {'、'.join(agents)}、effort {args.effort}、{args.mode}）")
    print(str(run_dir))


def mode_note(run: dict, key: str) -> str:
    if run["mode"] == "implement":
        wt = run["agents"][key].get("worktree", "")
        return (
            f"- 作業は自分専用の worktree `{wt}` で行う。ここはあなただけが触る場所である。\n"
            "- 変更はコミットせず、作業ツリーに残す。差分はオーケストレーターが集める。\n"
            "- ビルドやテストを実行できるなら実行し、結果の要点を最終メッセージに書く。"
        )
    return "- 調査と判断のタスクである。リポジトリのファイルを書き換えない。読むための道具は使える。ビルドはしない。"


def _phase_worker(run: dict, key: str, phase: str, prompt: str, cwd: Path,
                  resume: str | None, results: dict, lock: threading.Lock) -> None:
    agent = run["agents"][key]
    log(f"エージェント {key}（{agent['label']}）{phase} を開始")
    res = call_agent(run, agent, phase, prompt, cwd, resume)
    if not res.ok and resume:
        log(f"エージェント {key} の再開に失敗したため、新しいセッションで {phase} をやり直します: {res.error}")
        res = call_agent(run, agent, phase, prompt, cwd, None)
    with lock:
        results[key] = res
    state = "完了" if res.ok else f"失敗（{res.error}）"
    log(f"エージェント {key} {phase} {state}")


def run_phase(root: Path, run_dir: Path, run: dict, phase: str, only: list[str] | None) -> dict:
    keys = [k for k in run["agents"] if not only or k in only]
    preflight({run["agents"][k]["vendor"] for k in keys})
    results: dict[str, CallResult] = {}
    lock = threading.Lock()
    threads = []
    for key in keys:
        agent = run["agents"][key]
        cwd = root
        if run["mode"] == "implement":
            cwd = ensure_worktree(root, run, key)
        if phase == "run":
            prompt = render("task.md", TASK=read_text(run_dir / "task.md"),
                            MODE_NOTE=mode_note(run, key), RUN_DIR=str(run_dir.relative_to(root)),
                            **agent_vars(run))
            resume = None
        else:
            prompt = render("review.md", TASK=read_text(run_dir / "task.md"),
                            RESULTS=results_for_review(run_dir, run, exclude=key),
                            RUN_DIR=str(run_dir.relative_to(root)), **agent_vars(run))
            resume = agent.get("session_id")
        write_text(run_dir / "prompts" / f"{phase}-{key}.md", prompt)
        t = threading.Thread(target=_phase_worker,
                             args=(run, key, phase, prompt, cwd, resume, results, lock), daemon=True)
        t.start()
        threads.append(t)
    save_run(run_dir, run)  # worktree の情報を先に残す
    for t in threads:
        t.join()
    # ここで初めてファイルへ書く。全員が終わるまで成果はメモリにしかない。
    for key, res in results.items():
        agent = run["agents"][key]
        write_text(run_dir / "logs" / f"{phase}-{key}.stdout.txt", res.stdout)
        write_text(run_dir / "logs" / f"{phase}-{key}.stderr.txt", res.stderr)
        agent["status"][phase] = "ok" if res.ok else "failed"
        agent.setdefault("errors", {})[phase] = res.error
        agent.setdefault("resumed", {})[phase] = res.resumed
        run["usage"].setdefault(key, {})[phase] = res.usage
        if phase == "run" and res.session_id:
            agent["session_id"] = res.session_id
        if not res.ok:
            continue
        out_dir = "results" if phase == "run" else "reviews"
        write_text(run_dir / out_dir / f"{key}.md", res.text.rstrip() + "\n")
        if phase == "run" and run["mode"] == "implement":
            write_text(run_dir / "results" / f"{key}.patch", capture_patch(Path(agent["worktree"])))
    save_run(run_dir, run)
    return results


def results_for_review(run_dir: Path, run: dict, exclude: str) -> str:
    parts = []
    for key in run["agents"]:
        if key == exclude:
            continue
        p = run_dir / "results" / f"{key}.md"
        if not p.exists():
            parts.append(f"### エージェント {key}\n\n（成果なし。このエージェントは失敗した）\n")
            continue
        section = f"### エージェント {key}\n\n{read_text(p).strip()}\n"
        patch = run_dir / "results" / f"{key}.patch"
        if patch.exists():
            data = read_text(patch)
            if len(data.encode("utf-8")) <= PATCH_INLINE_LIMIT:
                section += f"\n#### エージェント {key} の差分\n\n```diff\n{data}\n```\n"
            else:
                section += f"\n#### エージェント {key} の差分\n\n差分が大きいので `{patch}` を直接読むこと。\n"
        parts.append(section)
    return "\n".join(parts)


def all_results(run_dir: Path, run: dict, sub: str) -> str:
    parts = []
    for key in run["agents"]:
        p = run_dir / sub / f"{key}.md"
        body = read_text(p).strip() if p.exists() else "（なし。このエージェントは失敗した）"
        parts.append(f"### エージェント {key}\n\n{body}\n")
    return "\n".join(parts)


def cmd_run(args: argparse.Namespace) -> None:
    root = repo_root()
    run_dir = Path(args.run_dir).resolve()
    run = load_run(run_dir)
    only = args.only.upper().split(",") if args.only else None
    run_phase(root, run_dir, run, "run", only)


def cmd_review(args: argparse.Namespace) -> None:
    root = repo_root()
    run_dir = Path(args.run_dir).resolve()
    run = load_run(run_dir)
    only = args.only.upper().split(",") if args.only else None
    done = [k for k, a in run["agents"].items() if a["status"].get("run") == "ok"]
    if len(done) < 2:
        raise SystemExit("成果が 2 体未満なので、レビューを始められません。")
    run_phase(root, run_dir, run, "review", only)


def cmd_synthesize(args: argparse.Namespace) -> None:
    root = repo_root()
    run_dir = Path(args.run_dir).resolve()
    run = load_run(run_dir)
    synth = run["synthesis"]
    table = "| 記号 | モデル |\n| --- | --- |\n" + "\n".join(
        f"| {k} | {a['label']}（`{a['model']}`） |" for k, a in run["agents"].items()
    )
    prompt = render("synthesis.md", TASK=read_text(run_dir / "task.md"),
                    RESULTS=all_results(run_dir, run, "results"),
                    REVIEWS=all_results(run_dir, run, "reviews"), AGENT_TABLE=table,
                    **agent_vars(run))
    write_text(run_dir / "prompts" / "synthesis.md", prompt)
    preflight({synth["vendor"]})
    agent = {"vendor": synth["vendor"], "model": synth["model"], "label": synth["label"]}
    log(f"総括（{synth['label']}）を開始")
    saved_mode = run["mode"]
    run["mode"] = "analysis"  # 総括は常に読み取り専用で走らせる
    res = call_agent(run, agent, "synthesis", prompt, root, None)
    run["mode"] = saved_mode
    write_text(run_dir / "logs" / "synthesis.stdout.txt", res.stdout)
    write_text(run_dir / "logs" / "synthesis.stderr.txt", res.stderr)
    run["usage"]["synthesis"] = res.usage
    synth["status"] = "ok" if res.ok else "failed"
    synth["error"] = res.error
    save_run(run_dir, run)
    if not res.ok:
        raise SystemExit(f"総括に失敗しました: {res.error}")
    write_text(run_dir / "summary.md", res.text.rstrip() + "\n")
    log(f"総括を書きました: {run_dir / 'summary.md'}")


def cmd_all(args: argparse.Namespace) -> None:
    cmd_run(argparse.Namespace(run_dir=args.run_dir, only=None))
    run = load_run(Path(args.run_dir).resolve())
    failed = [k for k, a in run["agents"].items() if a["status"].get("run") != "ok"]
    if failed:
        # 欠けたままレビューへ進むと、残りが少ない相手だけを見て批評し、総括もその上に載る。
        raise SystemExit(
            f"エージェント {'、'.join(failed)} の実行が失敗したので、レビューへ進まずに止めます。"
            f"原因を直してから `run {args.run_dir} --only {','.join(failed)}` を実行し、"
            f"続けて `review {args.run_dir}` と `synthesize {args.run_dir}` を実行してください。"
        )
    cmd_review(argparse.Namespace(run_dir=args.run_dir, only=None))
    cmd_synthesize(argparse.Namespace(run_dir=args.run_dir))


def cmd_status(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    run = load_run(run_dir)
    print(f"run: {run['run_id']}  mode: {run['mode']}  effort: {run['effort']['run']}  base: {run['base_commit'][:10]}")
    print("| 記号 | モデル | 実行 | レビュー | 再開 |")
    print("| --- | --- | --- | --- | --- |")
    for k, a in run["agents"].items():
        st = a.get("status", {})
        rs = a.get("resumed", {}).get("review")
        print(f"| {k} | {a['label']} | {st.get('run', '-')} | {st.get('review', '-')} | {rs if rs is not None else '-'} |")
    syn = run["synthesis"]
    print(f"総括: {syn['label']} {syn.get('status', '-')}")
    for k, phases in run.get("usage", {}).items():
        print(f"usage {k}: {json.dumps(phases, ensure_ascii=False)[:300]}")


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="claude と codex の実行ファイル、バージョン、ログイン状態を確かめる")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("init", help="実行ディレクトリと run.json を作る")
    p.add_argument("--slug", required=True, help="ディレクトリ名に使う短い英字（例: scroll-design）")
    p.add_argument("--task-file", help="タスク本文の Markdown ファイル")
    p.add_argument("--task", help="タスク本文")
    p.add_argument("--agents", help="使うエージェントをカンマ区切りで（既定 A,B,C,D）")
    p.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT, help="実行とレビューの effort（既定 high）")
    p.add_argument("--mode", choices=["analysis", "implement"], default="analysis")
    p.add_argument("--allow", help="implement モードで Claude に追加で許す道具（例: \"Bash(make:*),Bash(python:*)\"）")
    p.add_argument("--synth-model", help="総括のモデル（既定 claude-fable-5-1）")
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help=f"実行ディレクトリの親（既定 {DEFAULT_OUT_DIR}）")
    p.add_argument("--timeout", type=int, default=3600, help="1 呼び出しの上限秒数")
    p.set_defaults(func=cmd_init)

    for name, fn, help_ in (
        ("run", cmd_run, "エージェントを同時に走らせ、成果を results/ に集める"),
        ("review", cmd_review, "各エージェントに他の成果をレビューさせる"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("run_dir")
        p.add_argument("--only", help="やり直すエージェントをカンマ区切りで（例: C,D）")
        p.set_defaults(func=fn)

    p = sub.add_parser("synthesize", help="総括を書かせる")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_synthesize)

    p = sub.add_parser("all", help="run、review、synthesize を続けて行う")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("status", help="進行状態を表示する")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
