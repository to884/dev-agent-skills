# multi-agent-dispatch

同じタスクを Claude Code CLI と Codex CLI 経由で最大 4 体のエージェントに渡し、それぞれの結果を集めます。
設計案の比較や、障害の原因について独立した見解を集めたいときに使います。

## 実行の流れ

Python と、実行に使う CLI のログインが必要です。
両方の CLI を使う場合は、最初に `check` で実行ファイル、バージョン、ログイン状態を確認できます。

```powershell
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py check
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py init --slug design-review --task-file task.md --agents A,C
```

1. `init` に依頼を書いた Markdown ファイルとエージェントを渡します。例の `A,C` は Claude Opus 5.5 と GPT-6.1 Sol を指定します。
2. `run` に `init` が表示した実行ディレクトリを渡します。各エージェントの結果は、その中の `results/` にまとまります。
3. 必要に応じて、同じディレクトリを `review` と `synthesize` に渡します。`review` は互いの成果から欠陥や反例を探し、`synthesize` は根拠を突き合わせて `summary.md` を書きます。

手順 2 と 3 のコマンドでは、`<run-dir>` を `init` が表示したパスに置き換えてください。

```powershell
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py run "<run-dir>"
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py review "<run-dir>"
python .agents/skills/multi-agent-dispatch/scripts/dispatch.py synthesize "<run-dir>"
```

## 調査と実装

既定の `analysis` は調査やレビュー向けです。Claude への編集制限はプロンプトによる指示です。
実装を比較する場合は `init --mode implement` を使います。各エージェントが専用の Git worktree で編集し、差分が `results/` に保存されます。
実行ディレクトリは `docs/agent-runs/` 以下に作られます。`run.json` でモデル、実行状態、使用量を確認できます。

[スキル一覧に戻る](../../README.md) · [エージェント向けの手順（SKILL.md）](../../.agents/skills/multi-agent-dispatch/SKILL.md)
