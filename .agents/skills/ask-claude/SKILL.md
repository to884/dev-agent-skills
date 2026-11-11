---
name: ask-claude
description: Codex から、Claude Code CLI の Claude Opus 5.5 または Claude Fable 5.2 に 1 つのタスクを依頼し、最終メッセージを受け取る。別モデルの見解、レビュー、原因究明、実装の委任に使う。指定したファイルや差分の欠陥と反例を探す敵対的レビューも頼める。長い依頼は切り離して起動し、後で受け取る。
---

# Claude への依頼

Claude Code CLI（`claude -p`）に 1 つのタスクを渡し、最終メッセージだけを受け取る。
モデルは Claude Opus 5.5（既定）と Claude Fable 5.2、effort は medium、high、xhigh から選ぶ。
Fable 5.2 を選んで利用制限で使えなければ、Opus 5.5 に切り替えて依頼をやり直す。
対象を壊すことを役割とする敵対的レビューは `review` で頼む（下の「敵対的レビュー」）。

## 手順

スクリプトは `scripts/ask_claude.py`。作業ディレクトリは依頼の対象となるリポジトリのルートにする（既定は現在のディレクトリ。`--cwd` で指定できる）。

### 1. Claude CLI を確認する

```powershell
python .agents/skills/ask-claude/scripts/ask_claude.py check
```

実行ファイル、バージョン、ログイン状態、API ホストへの到達を表で出す。使えなければ終了コード 1 で止まるので、作者に `claude auth login` を実行してもらうか、下の「注意」のネットワークの項を確かめる。`ask` と `review` も呼び出しの前に同じ確認をする。

### 2. 依頼を書く

何を決めるか、または作るか。成果に何を含めるか。読むべきファイル。出力形式の指定。
数行を超える依頼はファイルに書き、`--task-file` で渡す。

### 3. 呼び出す

数分で終わる依頼は、そのまま呼んで標準出力から受け取る。

```powershell
python .agents/skills/ask-claude/scripts/ask_claude.py ask --task-file <path> --effort high
```

- 既定は Claude Opus 5.5。`--model fable` で Claude Fable 5.2。`--effort medium|high|xhigh`（既定 high）。
- Fable 5.2 が利用制限（429）で止まったら、同じ effort と mode で Opus 5.5 に切り替える。Fable がツールを呼ぶ前に止まったなら同じ依頼を最初から渡し、ツールを呼んだ後なら Fable のセッションを `--resume` で引き継いで続けさせる。切り替えたことは標準エラーに出し、`.status.json` の `fallback` に元のモデルとエラーを残す（`model` と `label` は切り替え後のものになる）。切り替えたくなければ `--no-fallback` を付ける。
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

## 敵対的レビュー

`review` は、指定した対象を壊すことを役割とするレビューを頼む。レビュアーは対象を褒めず、改善案も出さない。欠陥、反例、崩れる前提を、重大度と根拠を添えて並べる。
欠陥が見つからなければ「崩せなかった」と書くよう指示してあるので、指摘の数で対象の良し悪しを測らない。

```powershell
python .agents/skills/ask-claude/scripts/ask_claude.py review --target src/parser.py --target docs/design.md --task-file <path>
python .agents/skills/ask-claude/scripts/ask_claude.py review --diff --detach --out <file>
python .agents/skills/ask-claude/scripts/ask_claude.py review --diff main..HEAD --model fable --effort xhigh
```

- `--target <path>` はレビュー対象のファイルかディレクトリで、繰り返し指定できる。プロンプトにはパスだけを書き、レビュアーが自分で読む。パスは `--cwd` からの相対パスか絶対パスで書く。見つからなければ呼び出す前に止まる。
- `--diff [REV]` は `git diff REV`（REV を省くと `git diff HEAD`。ステージ済みと未ステージの変更）を取り込み、プロンプトに埋め込む。未追跡のファイルは含まないので、新しいファイルは `--target` で指定する。取り込んだ差分は `<out>.target.diff` に残す（`--out` が無ければ `%TEMP%\ask-claude\` の下。置き場所は標準エラーに出す）。200 KB を超える差分は埋め込まず、そのファイルを読ませる。
- `--target` と `--diff` は一緒に使える。どちらも無ければ止まる。
- `--task` / `--task-file` で、対象が果たすべき目的を渡す（任意）。省くと、レビュアーは目的を仮定し、仮定であることを書く。
- 常に読み取り専用の依頼として走る。`--mode` と `--allow` は無く、事前承認するのは `analysis` と同じ読み取り系の道具だけである。`--model`、`--effort`、`--out`、`--detach`、`--resume`、`--cwd`、`--timeout`、`--no-fallback` は `ask` と同じで、Fable 5.2 が利用制限で止まったときの Opus 5.5 への切り替えも同じに働く。切り離したら `wait` で受け取る。

最終メッセージは `## 判定`（採用可、条件付きで採用可、却下と確信度）、`## 致命的・重大な欠陥`、`## 崩れる前提`、`## 見落としと軽微な問題`、`## 検証した事実`、`## 崩せなかった点` の順に並ぶ。
指摘は根拠を確かめてから採る。「推測」と書かれた指摘は、確かめるまで採らない。

## 注意

- Codex の workspace-write サンドボックスは既定でネットワークを遮断するので、`claude` の API 呼び出しが接続拒否で失敗する（実測では約 3 分待ってから落ちた）。`check`、`ask`、`review` は先に API ホストへの到達を確かめて止まる。対処は、`~/.codex/config.toml` の `[sandbox_workspace_write]` に `network_access = true` を設定する（`-c sandbox_workspace_write.network_access=true` でも可。この設定で成功を確認した）か、Codex が求める承認でサンドボックス外の実行を許可する。
- 費用は呼び出しごとにかかる。同じ依頼を作者の指示なしに繰り返さない。`review` も `ask` と同じに費用がかかる。Opus 5.5 への切り替えは、利用制限で止まったときにだけ自動で 1 回行う。
- `--timeout` の既定は 1800 秒で、呼び出し 1 回ごとの上限である。超えると失敗として記録する。切り替えると合計で最長 2 倍かかる。
- `claude` の `--fallback-model` は過負荷やモデルが使えないときの切り替えで、利用制限（429）では切り替わらない（Claude Code 2.1.289 で確認）。そのためこのスキルは自前で切り替える。
- `claude` が PATH に無ければ `~/.local/bin/claude.exe` を使う。環境変数 `ASK_CLAUDE_EXE` で実行ファイルを指定できる。
- Claude はリポジトリの `CLAUDE.md` を自動で読むが、このスキルのプロンプト（`templates/task.md` と `templates/adversarial.md`）はそれに依存しない。
- `templates/adversarial.md` は ask-codex と同じ文面に保つ。片方を直したら、もう片方も直す。
- 取り込み規則、テンプレート、`review` のテスト: `python -m unittest discover -s .agents/skills/ask-claude/scripts -p "test_*.py"`
