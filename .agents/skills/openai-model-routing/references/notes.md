# 典拠と実測値

`SKILL.md` の表と数値の出どころ。OpenAI がモデルを増減したとき、Codex を更新したときに見直す。

## 典拠と確認日

| 項目 | 典拠 | 確認日 |
| --- | --- | --- |
| GPT-6 Astra の単価、文脈窓、effort（`none` は 400） | https://developers.openai.com/api/docs/models/gpt-6-astra | 2026-09-29 |
| GPT-6 Sol | https://developers.openai.com/api/docs/models/gpt-6-sol | 2026-09-29 |
| GPT-6 Luna | https://developers.openai.com/api/docs/models/gpt-6-luna | 2026-09-29 |
| GPT-5.6 Sol（$4・$20 は 2026-11-21 までの販促価格） | https://developers.openai.com/api/docs/models/gpt-5.6-sol | 2026-09-29 |
| API の入力総量が 272K 超のリクエストは入力とキャッシュ 2 倍、出力 1.5 倍 | 上の 4 ページ | 2026-09-29 |
| effort の段の意味 | https://developers.openai.com/api/docs/guides/reasoning | 2026-09-29 |
| キャッシュ：読み 0.1 倍、書き 1.25 倍、最小 1,024 トークン、最後の書き込み・再利用から最低 30 分保持（延長される場合あり）、モデルを変えると再利用不可、GPT-6 では `configuration_update` で effort を変えても前置きを保てる | https://developers.openai.com/api/docs/guides/prompt-caching | 2026-09-29 |
| Astra の位置づけ、出力トークンが少ない、テストを徹底する傾向 | https://developers.openai.com/api/docs/guides/latest-model | 2026-09-29 |
| Codex のモデル一覧と段（Light〜Max。Ultra は Astra と Sol、Luna は不可） | https://learn.chatgpt.com/docs/models | 2026-09-29 |
| `model_reasoning_effort` の値（low〜ultra、モデルとクライアントに依存） | https://learn.chatgpt.com/docs/config-file/config-reference | 2026-09-29 |
| allowance の仕組み、5 時間あたりの推定メッセージ数、effort の指針 | https://help.openai.com/en/articles/20001516-managing-usage-with-gpt-6-astra-in-work-and-codex（2026-09 中旬更新） | 2026-09-29 |
| この環境の Codex：codex-cli 0.158.0-alpha.2.1、ChatGPT ログイン、`config.toml` は `gpt-6-astra` / `medium` / `service_tier = "default"` | 実測 | 2026-09-29 |
| `ask-codex`：astra→`gpt-6-astra`、sol→`gpt-5.6-sol`、モデル ID の直渡し可、effort は `medium|high|xhigh`、既定 `high` | `.agents/skills/ask-codex/scripts/ask_codex.py` | 2026-09-29 |
| `dispatch`：C=`gpt-5.6-sol`、D=`gpt-6-astra`、effort は同じ 3 段、`run.json` の各エージェントで段階ごとに上書き可 | `.agents/skills/multi-agent-dispatch/scripts/dispatch.py` | 2026-09-29 |
| `ultra` が `codex exec` で通るか | 未確認 | — |
| GPT-6 Sol と Luna の allowance 消費（Help の表は GPT-5.6 系のみ） | 未確認 | — |
| 同じタスクでのモデル間の allowance 消費比、および API の長文入力の割増倍率が allowance にも適用されるか | 未確認 | — |
| Codex CLI が effort 変更時に `configuration_update` を使うか | 未確認 | — |
| 作者のプラン（Plus / Pro 5x / Pro 20x）と残量 | 未確認 | — |
| effort の段ごとの品質と費用の曲線（GPT-6） | 未計測 | — |

## 単価（API、$/1M トークン）

| モデル | 入力 | キャッシュ読み | 出力 | 文脈窓 | 最大出力 | effort |
| --- | --- | --- | --- | --- | --- | --- |
| GPT-6 Astra | 10 | 1 | 50 | 1,050,000 | 128,000 | `low` `medium` `high` `xhigh` `max` |
| GPT-6 Sol | 2 | 0.2 | 10 | 1,050,000（入力は 922,000 まで） | 128,000 | `none`〜`max` |
| GPT-6 Luna | 0.1 | 0.01 | 0.5 | 同上 | 128,000 | `none`〜`max` |
| GPT-5.6 Sol | 4 | 0.4 | 20 | 1,050,000 | 128,000 | `none`〜`max` |

キャッシュ書き込みは入力の 1.25 倍。API の入力総量が 272K 超のリクエストは、その全体が割増。材料だけでなく、指示、ツール定義、履歴、ツール結果も入力総量に含める。ChatGPT ログインの allowance に同じ倍率が適用されるかは未確認。
Codex の一覧には GPT-5.5（2026-10-14 に退役）、GPT-5.4、GPT-5.4 mini もあるが、候補にしない。

## allowance（ChatGPT ログイン）

5 時間窓あたりの推定ローカルメッセージ数。OpenAI Help の値で、固定の上限ではない。実際の消費はタスク、モデル、設定で変わる。

| モデル | Plus | Pro 5x | Pro 20x | Business Standard |
| --- | --- | --- | --- | --- |
| GPT-6 Astra | 5〜45 | 25〜225 | 100〜900 | 5〜45 |
| GPT-5.6 Sol | 10〜100 | 50〜500 | 200〜2,000 | 10〜100 |
| GPT-5.6 Terra | 25〜200 | 125〜1,000 | 500〜4,000 | 25〜200 |
| GPT-5.6 Luna | 250〜2,000 | 1,250〜10,000 | 5,000〜40,000 | 250〜2,000 |

推定メッセージ数の比の目安は Astra : 5.6 Sol : 5.6 Luna ≈ 1 : 2 : 50。同じタスクでの allowance 消費比を測った値ではなく、その倍率を示すものではない。5 時間窓と週窓の両方に残りが要り、5 時間窓は週の残りがあっても先に尽きる。
モデルを切り替えても allowance は戻らない。Fast mode（`service_tier = "fast"`）は allowance を多く使う。
GPT-6 Sol と Luna の行は Help の表に無い。API 単価の比（Astra の 1/5、1/100）から推測せず、未確認のまま扱う。

## OpenAI が書いている effort の指針（計測値ではない）

- Astra の `low` が Sol の `high` を上回ることがある。Sol の `high` で満足なら Astra の `low` か `medium` から試す
- 上げると allowance を多く使い、結果が良くなるとは限らない
- 足りない情報や権限は effort では補えない
- 段は allowance の固定量を決めるものではない
- Astra は出力トークンが少なく、タスクあたりの費用は前世代より安いことがある

## 測り方

`claude-model-routing/references/notes.md` と同じ。設定ごとに別セッション、effort 以外はバイト単位で同一、難しい事例を混ぜる、繰り返し試行で確かめる。GPT-6 の曲線はまだ測っていない。
