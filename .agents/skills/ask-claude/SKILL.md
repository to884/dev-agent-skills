---
name: ask-claude
description: Codex から、Claude Code CLI の Claude Fable 5.1 または Claude Opus 5.5 に 1 つのタスクを依頼し、最終メッセージを受け取る。別モデルの見解、レビュー、原因究明、実装の委任に使う。長い依頼は切り離して起動し、後で受け取る。
---

# Claude への依頼

Claude Code CLI（`claude -p`）に 1 つのタスクを渡し、最終メッセージだけを受け取る。
モデルは Claude Fable 5.1（既定）と Claude Opus 5.5、effort は medium、high、xhigh から選ぶ。

## 手順

スクリプトは `scripts/ask_claude.py`。作業ディレクトリは依頼の対象となるリポジトリのルートにする（既定は現在のディレクトリ。`--cwd` で指定できる）。

### 1. Claude CLI を確認する

```powershell
python .agents/skills/ask-claude/scripts/ask_claude.py check
```

実行ファイル、バージョン、ログイン状態、API ホストへの到達を表で出す。使えなければ終了コード 1 で止まるので、作者に `claude auth login` を実行してもらうか、下の「注意」のネットワークの項を確かめる。`ask` も呼び出しの前に同じ確認をする。

### 2. 依頼を書く

何を決めるか、または作るか。成果に何を含めるか。読むべきファイル。出力形式の指定。
数行を超える依頼はファイルに書き、`--task-file` で渡す。

### 3. 呼び出す

数分で終わる依頼は、そのまま呼んで標準出力から受け取る。

```powershell
python .agents/skills/ask-claude/scripts/ask_claude.py ask --task-file <path> --model fable --effort high
```

- `--model opus` で Claude Opus 5.5。`--effort medium|high|xhigh`（既定 high）。
- `claude` は常に `--permission-mode auto`（Auto）で起動する。`--mode analysis`（既定）と `--mode edit` の違いは、事前承認する道具の範囲とプロンプトの指示だけで、権限モードは同じ。`analysis` は読み取りだけを依頼する（書き換えの禁止はプロンプトの指示であり、権限では強制しない）。`edit` は作業ツリーの編集を依頼する。ビルドやテストのコマンドも事前承認するなら `--allow "Bash(make:*),Bash(python:*)"` のように足す。
- `--out <file>` で最終メッセージをファイルにも書く。

時間のかかる依頼（high や xhigh、調査や実装）は、シェルコマンドのタイムアウトを超える。切り離して起動し、`wait` で受け取る。

```powershell
python .agents/skills/ask-claude/scripts/ask_claude.py ask --task-file <path> --detach --out <file>
python .agents/skills/ask-claude/scripts/ask_claude.py wait <file>
```

`wait` は最長 570 秒待ち、終わっていれば本文を出す。まだなら終了コード 3 で戻るので、もう一度 `wait` を実行する。`status <file>` で状態だけを見る。
`--out` を省くと `%TEMP%\ask-claude\` に書く。結果の隣に `.status.json`、`.log`、`.task.md` ができる。

### 4. 結果を扱う

返ってきた内容は根拠を確かめてから使う。`edit` モードでは `git diff` で変更を確かめる。
続けて聞くには、標準エラーに出た session ID を `--resume <id>` に渡す。同じ作業ディレクトリから呼ぶこと。

## 注意

- Codex の workspace-write サンドボックスは既定でネットワークを遮断するので、`claude` の API 呼び出しが接続拒否で失敗する（実測では約 3 分待ってから落ちた）。`check` と `ask` は先に API ホストへの到達を確かめて止まる。対処は、`~/.codex/config.toml` の `[sandbox_workspace_write]` に `network_access = true` を設定する（`-c sandbox_workspace_write.network_access=true` でも可。この設定で成功を確認した）か、Codex が求める承認でサンドボックス外の実行を許可する。
- 費用は呼び出しごとにかかる。同じ依頼を作者の指示なしに繰り返さない。
- `--timeout` の既定は 1800 秒。超えると失敗として記録する。
- `claude` が PATH に無ければ `~/.local/bin/claude.exe` を使う。環境変数 `ASK_CLAUDE_EXE` で実行ファイルを指定できる。
- Claude はリポジトリの `CLAUDE.md` を自動で読むが、このスキルのプロンプト（`templates/task.md`）はそれに依存しない。
- 取り込み規則のテスト: `python -m unittest discover -s .agents/skills/ask-claude/scripts -p "test_*.py"`
