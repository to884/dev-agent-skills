---
name: multi-agent-dispatch
description: 1 つのタスクを Claude Code（Opus 5.5、Fable 5.2）と Codex（GPT-6.2 Sol、GPT-6.2 Astra）のエージェントへ CLI 経由で同時に依頼し、成果を集める。任意で敵対的な相互レビューと総括まで続ける。設計判断、原因究明、レビュー、実装案の比較など、複数モデルの独立した見解や実装が欲しいときに使う。成果物は docs/agent-runs/ に残す。
---

# 複数エージェントへのタスク依頼

同じタスクを最大 4 体のエージェント（`claude -p` と `codex exec`）に独立に解かせ、成果を 1 つのディレクトリに集める。
必要なら、各エージェントに他の成果を敵対的にレビューさせ、総括を 1 本にまとめる。
各エージェントは他のエージェントの過程も成果も見ない。成果は全員が終わってから書き出す。

## エージェント

| 記号 | CLI | モデル |
| --- | --- | --- |
| A | Claude Code | `claude-opus-5-5`（Claude Opus 5.5） |
| B | Claude Code | `claude-fable-5-2`（Claude Fable 5.2） |
| C | Codex | `gpt-6.2-sol`（GPT-6.2 Sol） |
| D | Codex | `gpt-6.2-astra`（GPT-6.2 Astra） |

effort は `--effort medium|high|xhigh`（既定 high）で選び、実行とレビューに使う。総括は Claude Fable 5.2 の medium で書く。
使うエージェントは `--agents A,C` のように絞れる。モデルと effort は `run.json` を編集しても変えられる。

## 置き場所

```text
docs/agent-runs/YYYY-MM-DD-<slug>/
  run.json     設定と進行状態（モデル、effort、セッション ID、状態、使用量）
  task.md      依頼したタスク
  prompts/     各エージェントへ実際に渡したプロンプト
  results/     成果 A.md〜D.md（implement モードでは A.patch〜D.patch も）
  reviews/     敵対的な相互レビュー A.md〜D.md（任意）
  summary.md   総括（任意）
  logs/        CLI の生出力（.gitignore 済み）
```

## 手順

スクリプトは `scripts/dispatch.py` で、リポジトリ内のどこから呼んでもよい。以下は PowerShell から実行する。

### 0. 両方の CLI を確認する

```powershell
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py check
```

`claude` と `codex` の実行ファイル、バージョン、ログイン状態を表で出す。どちらかが欠けるか未ログインなら終了コード 1 で止まるので、作者に `claude auth login` または `codex login` を実行してもらう。
`init` と各段階も、使う CLI について同じ確認を行ってから走る。

### 1. タスクを書く

作者の指示をそのまま `task.md` に写す。補足は作者に確認してから足す。
「何を決めるか、または作るか」「成果に何を含めるか」「読むべき文書やコード」を含める。

### 2. 初期化

```powershell
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py init --slug <slug> --task-file <path> --effort high
```

`--task "<本文>"` でも渡せる。`--agents A,B,C,D` で使うエージェントを選ぶ。
Claude は常に `--permission-mode auto`（Auto）で起動する。モードによる違いは事前承認する道具の範囲とプロンプトの指示だけである。
`--mode analysis`（既定）はリポジトリを書き換えない依頼で、書き換えの禁止はプロンプトの指示であり、Claude の権限では強制しない。
`--mode implement` では各エージェントが専用の worktree（`.agents/worktrees/<run>-<記号>`）で作業し、差分を `results/<記号>.patch` に集める。Claude にビルドやテストのコマンドを事前承認するなら `--allow "Bash(make:*),Bash(python:*)"` のように足す。Codex は `workspace-write` サンドボックスで動く。

### 3. 実行

```powershell
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py run <run-dir>
```

選んだエージェントを同時に走らせ、全員が終わってから `results/` に書く。
失敗したエージェントは `run.json` に `failed` と記録され、`--only C` のようにやり直せる。やり直すエージェントは他の成果をリポジトリで見られるので、独立性を保つなら `results/` を空にして全員をやり直す。

### 4. 敵対的な相互レビュー（任意）

```powershell
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py review <run-dir>
```

各エージェントは、自分以外の成果に対して、要件や資料に照らして欠陥や反例を探す。指摘ごとに重大度、対象、根拠、問題、検証方法、修正の方向性を書き、各成果の判定を `Final Result` に示す。同じ指摘が自分の成果にも当てはまるか確かめる。詳細は [templates/review.md](templates/review.md) にある。

実行時のセッションを再開し、自分の調査の文脈を引き継ぐ。相互レビューでは、Claude に読み取りだけを指示し、読み取り系の道具を事前承認する。Codex は読み取り専用のサンドボックスで動く。再開に失敗したら新しいセッションで行い、`run.json` に `resumed: false` と記す。

### 5. 総括（任意）

```powershell
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py synthesize <run-dir>
```

総括役が `task.md`、`results/`、`reviews/` を読み、各指摘の反証を試みてから `summary.md` を書く。要件の読み違い、実際には起こりえない条件、既存の対策などを確認する。問題が確認できた指摘を修正対象とし、覆せた指摘は棄却する。判断に必要な情報が足りなければ保留し、追加で確認することを書く。各判断の根拠を記録する。詳細は [templates/synthesis.md](templates/synthesis.md) にある。

総括を省いて相互レビューの指摘を使う場合は、結果を受け取った担当者が同じ検証を行う。実際の修正は依頼の範囲に含まれる場合に行う。
`all <run-dir>` で 3〜5 を続けて行う。実行で 1 体でも失敗したら、レビューへ進まずに止まる。

### 6. 作者へ報告する

`summary.md`（総括を省いたなら `results/` と、実施した場合は `reviews/`）を読み、結論、修正対象として確認した指摘、批評で動いた点、保留事項と作者の判断が要る点の順に書く。記号にはモデル名を添える。`run.json` の `usage`（トークンと費用）を 1 行添える。

## 注意

- 費用はエージェント数 × 段階数ぶんかかる。作者の指示なしに `all` を繰り返さない。
- 1 段階は数分から数十分かかる。`--timeout` の既定は 3600 秒。進行は `status <run-dir>` で見る。
- `codex` が PATH に無ければ `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe` の最新を使う。環境変数 `DISPATCH_CLAUDE` と `DISPATCH_CODEX` で実行ファイルを指定できる。
- Claude Code のセッション内から呼んでもよい。スクリプトが親の `CLAUDE*` 環境変数を外してから子の `claude -p` を起こす。
- 各 CLI はリポジトリの `CLAUDE.md` や `AGENTS.md` を自動で読むが、このスキルのプロンプトはそれらに依存しない。
- 成果が文の途中から始まっていたら取り込み不良である。取り込み規則は `scripts/test_dispatch.py` が固定している。

```powershell
python -m unittest discover -s .agents/skills/multi-agent-dispatch/scripts -p "test_*.py"
```
