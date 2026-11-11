# openai-model-routing

Codex CLI にタスクを渡す前に、OpenAI のモデルと reasoning effort を選ぶためのスキルです。
たとえば「既存パターンに沿って変更し、テストで検証できる作業を Codex に委譲するとき、どのモデルと effort を使うか判断してください」と依頼します。

## このスキルでの割り当て

| モデル | 条件 |
| --- | --- |
| GPT-6 Luna | 作業量に対して判断が少なく、合格条件が決まっていて成果を機械的に検査できる |
| GPT-6.1 Sol | 既存の手順やパターンに沿い、合格条件が決まっていて成果を機械的に検査できる |
| GPT-6 Astra | 既定。上の条件に該当しない作業 |

モデルを変える前に、現在のモデルで effort を調整できるかを考えます。
費用を見積もるときは、ChatGPT ログインで使うプランの利用枠と、API キーで使うトークン従量課金を分けて扱います。

## 委譲時に effort を明示する

このスキルの既定は Astra の `medium` です。`ask-codex` と `multi-agent-dispatch` のスクリプトは `high` が既定なので、選んだ値を明示します。

```powershell
python .agents/skills/ask-codex/scripts/ask_codex.py ask --task-file task.md --model astra --effort medium
```

`multi-agent-dispatch` で全員に同じ値を適用するには `init --effort` を使います。エージェントごとに変える場合は、実行前に `run.json` の対象エージェントを編集します。

[スキル一覧に戻る](../../README.md) · [エージェント向けの手順（SKILL.md）](../../.agents/skills/openai-model-routing/SKILL.md) · [料金と利用枠の参照資料](../../.agents/skills/openai-model-routing/references/notes.md)
