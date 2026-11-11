# ask-codex

Claude Code から Codex CLI に 1 件のタスクを渡し、最終メッセージを受け取ります。
設計のレビュー、原因調査、実装の依頼に使えます。Claude Code CLI とデスクトップアプリの Code タブで手順は共通です。

## 実行例

Python とログイン済みの Codex CLI が必要です。
リポジトリのルートで `check` を実行すると、CLI のバージョン、ログイン状態、API ホストへの接続を確認できます。

```powershell
python .agents/skills/ask-codex/scripts/ask_codex.py check
python .agents/skills/ask-codex/scripts/ask_codex.py ask --task "既存テストを読み、境界値の検証が不足している箇所を挙げてください"
```

長い依頼は `--task-file` で渡せます。
時間がかかる場合は `--detach --out result.md` でバックグラウンド実行し、`wait result.md` で結果を受け取ります。

## モデルと編集範囲

スクリプトの既定は GPT-6 Astra です。`--model sol` で GPT-6.1 Sol を指定できます。
既定の `analysis` では読み取り専用で実行します。作業ツリーの編集を許可する場合は `ask --mode edit` を使います。
モデルと effort の選び方は別ページにまとめています。[openai-model-routing](openai-model-routing.md)

## 欠陥や反例を探す

`review` は読み取り専用で動きます。`--diff` で Git の差分、`--target` でファイルやディレクトリを指定できます。
`--diff` だけを指定すると `git diff HEAD` を対象にします。未追跡ファイルは `--target` で指定します。

```powershell
python .agents/skills/ask-codex/scripts/ask_codex.py review --diff
```

[スキル一覧に戻る](../../README.md) · [エージェント向けの手順（SKILL.md）](../../.agents/skills/ask-codex/SKILL.md)
