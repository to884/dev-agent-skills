---
name: ask-codex
description: Claude Code（CLI とデスクトップアプリ）から、Codex CLI の GPT-6 Astra または GPT-6.1 Sol に 1 つのタスクを依頼し、最終メッセージを受け取る。別モデルの見解、レビュー、原因究明、実装の委任に使う。指定したファイルや差分の欠陥と反例を探す敵対的レビューも頼める。長い依頼は切り離して起動し、後で受け取る。
---

# Codex への依頼

Codex CLI（`codex exec`）に 1 つのタスクを渡し、最終メッセージだけを受け取る。
モデルは GPT-6 Astra（既定）と GPT-6.1 Sol、effort は medium、high、xhigh から選ぶ。
対象の欠陥や反例を探す敵対的レビューは `review` で頼む（下の「敵対的レビュー」）。
Claude Code の CLI からもデスクトップアプリの Code タブからも、手順は同じ。

## 手順

スクリプトは `scripts/ask_codex.py`。作業ディレクトリは依頼の対象となるリポジトリのルートにする（既定は現在のディレクトリ。`--cwd` で指定できる）。

### 1. Codex CLI を確認する

```powershell
python .agents/skills/ask-codex/scripts/ask_codex.py check
```

実行ファイル、バージョン、ログイン状態、API ホスト（chatgpt.com または api.openai.com）への到達を表で出す。使えなければ終了コード 1 で止まるので、作者に `codex login` を実行してもらうか、サンドボックスや proxy の設定を確かめる。`ask` と `review` も呼び出しの前に同じ確認をする。

### 2. 依頼を書く

何を決めるか、または作るか。成果に何を含めるか。読むべきファイル。出力形式の指定。
数行を超える依頼はファイルに書き、`--task-file` で渡す。

### 3. 呼び出す

数分で終わる依頼は、そのまま呼んで標準出力から受け取る。

```powershell
python .agents/skills/ask-codex/scripts/ask_codex.py ask --task-file <path> --model astra --effort high
```

- `--model sol` で GPT-6.1 Sol。`--effort medium|high|xhigh`（既定 high）。
- `--mode analysis`（既定）は読み取り専用。`--mode edit` は作業ツリーの編集を許す（Codex は workspace-write サンドボックス）。
- `--out <file>` で最終メッセージをファイルにも書く。

時間のかかる依頼（high や xhigh、調査や実装）は、Bash ツールのタイムアウト（最長 10 分）を超える。切り離して起動し、`wait` で受け取る。

```powershell
python .agents/skills/ask-codex/scripts/ask_codex.py ask --task-file <path> --detach --out <file>
python .agents/skills/ask-codex/scripts/ask_codex.py wait <file>
```

`wait` は最長 570 秒待ち、終わっていれば本文を出す。まだなら終了コード 3 で戻るので、もう一度 `wait` を実行する。`status <file>` で状態だけを見る。
`--out` を省くと `%TEMP%\ask-codex\` に書く。結果の隣に `.status.json`、`.log`、`.task.md` ができる。

### 4. 結果を扱う

返ってきた内容は根拠を確かめてから使う。`edit` モードでは `git diff` で変更を確かめる。
続けて聞くには、標準エラーに出た session ID を `--resume <id>` に渡す。

## 敵対的レビュー

`review` は、文書、設計、計画、コードなどを対象に、要件や資料に照らして欠陥や反例を探す。報告の形式と確認する観点は [templates/adversarial.md](templates/adversarial.md) にある。

```powershell
python .agents/skills/ask-codex/scripts/ask_codex.py review --target src/parser.py --target docs/design.md --task-file <path>
python .agents/skills/ask-codex/scripts/ask_codex.py review --diff --detach --out <file>
python .agents/skills/ask-codex/scripts/ask_codex.py review --diff main..HEAD --model sol --effort xhigh
```

- `--target <path>` はレビュー対象のファイルかディレクトリで、繰り返し指定できる。プロンプトにはパスだけを書き、レビュアーが自分で読む。パスは `--cwd` からの相対パスか絶対パスで書く。見つからなければ呼び出す前に止まる。
- `--diff [REV]` は `git diff REV`（REV を省くと `git diff HEAD`。ステージ済みと未ステージの変更）を取り込み、プロンプトに埋め込む。未追跡のファイルは含まないので、新しいファイルは `--target` で指定する。取り込んだ差分は `<out>.target.diff` に残す（`--out` が無ければ `%TEMP%\ask-codex\` の下。置き場所は標準エラーに出す）。200 KB を超える差分は埋め込まず、そのファイルを読ませる。
- `--target` と `--diff` は一緒に使える。どちらも無ければ止まる。
- `--task` / `--task-file` で、対象が果たすべき目的を渡す（任意）。省くと、レビュアーは目的を仮定し、仮定であることを書く。
- 常に読み取り専用で走る。`--mode` は無く、Codex は read-only サンドボックスで動く。`--resume` で再開するときも、元のセッションの設定を `-c sandbox_mode="read-only"` で上書きする。`--model`、`--effort`、`--out`、`--detach`、`--resume`、`--cwd`、`--timeout` は `ask` と同じで、切り離したら `wait` で受け取る。

最終メッセージは、指摘ごとの重大度、対象、根拠、問題、検証方法、修正の方向性を含む。レビュアーの判定は末尾の `Final Result: APPROVED` または `Final Result: REJECTED` に示される。

### 修正前の反証

レビューを受け取った担当者は、各指摘について、修正に着手する前に反証を試みる。要件の読み違い、実際には起こりえない条件、既存の対策など、指摘を覆す根拠がないかを調べる。要件、該当箇所、検証結果に照らして判断し、その根拠を記録する。

- 問題を確認できた場合は、修正対象とする。修正は依頼の範囲に含まれる場合に行う。
- 指摘を覆す根拠を確認できた場合は、指摘を棄却する。
- 情報が足りず判断できない場合は、追加で確認することを記録して保留する。

## 注意

- 費用は呼び出しごとにかかる。同じ依頼を作者の指示なしに繰り返さない。`review` も `ask` と同じに費用がかかる。
- `--timeout` の既定は 1800 秒。超えると失敗として記録する。
- `codex` が PATH に無ければ `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe` の最新を使う。環境変数 `ASK_CODEX_EXE` で実行ファイルを指定できる。
- Codex はリポジトリの `AGENTS.md` を自動で読むが、このスキルのプロンプト（`templates/task.md` と `templates/adversarial.md`）はそれに依存しない。
- `templates/adversarial.md` は ask-claude と同じ文面に保つ。片方を直したら、もう片方も直す。
- 取り込み規則、テンプレート、`review` のテスト: `python -m unittest discover -s .agents/skills/ask-codex/scripts -p "test_*.py"`
