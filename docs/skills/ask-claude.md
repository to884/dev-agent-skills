# ask-claude

Codex から Claude Code CLI に 1 件のタスクを渡し、最終メッセージを受け取ります。
設計案に別の見解が欲しいとき、障害の原因を調べたいとき、実装を任せたいときに使います。

## 実行例

Python とログイン済みの Claude Code CLI が必要です。
リポジトリのルートで `check` を実行すると、CLI のバージョン、ログイン状態、API ホストへの接続を確認できます。

```powershell
python .agents/skills/ask-claude/scripts/ask_claude.py check
python .agents/skills/ask-claude/scripts/ask_claude.py ask --task "このリポジトリの CLI スクリプトを読み、起動前の環境チェックで失敗する条件を挙げてください"
```

長い依頼は `--task-file` で渡せます。
時間がかかる場合は `--detach --out result.md` でバックグラウンド実行し、`wait result.md` で結果を受け取ります。

## モデルと編集範囲

既定は Claude Opus 5.5 です。`--model fable` で Claude Fable 5.2 を指定できます。
Fable 5.2 が利用制限に達すると Opus 5.5 で再試行します。この切り替えは `--no-fallback` で無効にできます。
既定の `analysis` と `review` は、プロンプトで読み取りのみを依頼します。
作業ツリーの編集を依頼する場合は `ask --mode edit` を使います。

## 欠陥や反例を探す

`review` にファイルやディレクトリを渡すには `--target`、Git の差分を渡すには `--diff` を使います。
`--diff` だけを指定すると `git diff HEAD` を対象にします。未追跡ファイルは `--target` で指定します。

```powershell
python .agents/skills/ask-claude/scripts/ask_claude.py review --diff
```

[スキル一覧に戻る](../../README.md) · [エージェント向けの手順（SKILL.md）](../../.agents/skills/ask-claude/SKILL.md)
