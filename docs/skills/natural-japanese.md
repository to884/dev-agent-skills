# natural-japanese

議事録、調査レポート、社内ガイド、技術記事などを読みやすい日本語で書くためのスキルです。
既存文書を直すときは、元の事実や数値、書き手の確信の度合いを保ちます。

## 書く、直す、診断する

```text
/natural-japanese write quick 会議の文字起こしから議事録を作ってください
/natural-japanese full README.md を推敲してください
/natural-japanese score quick README.md
```

`quick` は日常の文書向けの既定モードです。`full` では構成、用語、読みやすさまで詳しく点検します。
`score` は本文を書き換えず、文章の自然さを点数と理由で返します。
診断専用の `exp` は、実験的な検出器を使って隣り合う文の意味の近さも調べます。初回は約 1 GB のモデルをダウンロードします。

## 文章を機械的に点検する

点検スクリプトは `uv` で実行できます。次の例は技術文書向けの設定で指摘を出します。

```powershell
uv run .agents/skills/natural-japanese/scripts/lint.py --genre tech README.md
```

指摘には文脈によって自然な表現も含まれるため、直すかどうかは前後の文を読んで判断します。
`uv` が使えない環境では、目視用のチェックリストを使えます。
[目視用チェックリスト](../../.agents/skills/natural-japanese/references/manual-checklist.md) · [診断と採点の手順](../../.agents/skills/natural-japanese/references/diagnose.md)

[スキル一覧に戻る](../../README.md) · [エージェント向けの手順（SKILL.md）](../../.agents/skills/natural-japanese/SKILL.md)
