# 典拠と実測値

`SKILL.md` の表と数値の出どころ。OpenAI がモデルを増減したとき、Codex を更新したときに見直す。

## 典拠と確認日

| 項目 | 典拠 | 確認日 |
| --- | --- | --- |
| GPT-6.2 Astra の API 単価と文脈窓 | [モデルページ](https://developers.openai.com/api/docs/models/gpt-6.2-astra) | 2026-11-18 |
| GPT-6.2 Sol の API 単価と文脈窓 | [モデルページ](https://developers.openai.com/api/docs/models/gpt-6.2-sol) | 2026-11-18 |
| GPT-6 Sol（旧モデル） | [モデルページ](https://developers.openai.com/api/docs/models/gpt-6-sol) | 2026-10-31 |
| GPT-6 Luna | [モデルページ](https://developers.openai.com/api/docs/models/gpt-6-luna) | 2026-10-31 |
| GPT-6.2 の API 長文入力の割増 | 上の GPT-6.2 各モデルのページ | 2026-11-18 |
| GPT-6.2 の API で使える effort | [API モデル一覧](https://developers.openai.com/api/docs/models) | 2026-11-18 |
| API のキャッシュ単価、保持期間、`configuration_update` | [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) | 2026-11-18 |
| Codex のモデル選択と effort | [Codex CLI のモデル一覧](https://learn.chatgpt.com/docs/models?surface=cli) | 2026-11-18 |
| `model_reasoning_effort` の設定値はモデルやクライアントによる | [設定リファレンス](https://learn.chatgpt.com/docs/config-file/config-reference) | 2026-09-29 |
| Work と Codex の共有 allowance と 5 時間あたりの推定メッセージ数 | [Codex の料金と利用枠](https://learn.chatgpt.com/docs/pricing) | 2026-11-18 |
| この環境の Codex のモデル一覧に `gpt-6.2-astra` と `gpt-6.2-sol` があり、両方に `ultra` が掲載されている | `~/.codex/models_cache.json`（クライアント版 0.169.0） | 2026-11-18 |
| `ask-codex` の `astra` と `sol` は GPT-6.2 のモデル ID に解決される | [ask_codex.py](../../ask-codex/scripts/ask_codex.py) | 2026-11-18 |
| `dispatch` の C は `gpt-6.2-sol`、D は `gpt-6.2-astra` | [dispatch.py](../../multi-agent-dispatch/scripts/dispatch.py) | 2026-11-18 |
| `ultra` が `codex exec` で通るか | 未確認 | — |
| 同じタスクでのモデル間の allowance 消費比、および API の長文入力の割増倍率が allowance にも適用されるか | 未確認 | — |
| Codex CLI が effort 変更時に `configuration_update` を使うか | 未確認 | — |
| 作者のプランと残量 | 未確認 | — |
| effort の段ごとの品質と費用 | 未計測 | — |

## 単価（API の Standard、短い文脈、$/1M トークン）

| モデル | 入力 | キャッシュ読み | 出力 | 文脈窓 | 最大出力 | effort |
| --- | --- | --- | --- | --- | --- | --- |
| GPT-6.2 Astra | 8 | 0.40 | 40 | 1,050,000（入力は 922,000 まで） | 128,000 | `low` `medium` `high` `xhigh` `max` |
| GPT-6.2 Sol | 2 | 0.10 | 10 | 1,050,000（入力は 922,000 まで） | 128,000 | `low` `medium` `high` `xhigh` `max` |
| GPT-6 Sol（旧モデル） | 2 | 0.2 | 10 | 1,050,000（入力は 922,000 まで） | 128,000 | `none`〜`max` |
| GPT-6 Luna | 0.1 | 0.01 | 0.5 | 1,050,000（入力は 922,000 まで） | 128,000 | `none`〜`max` |

30 分保持のキャッシュ書き込みは入力の 1.25 倍。GPT-6.2 の API では入力が 272K を超えるリクエスト全体に、入力とキャッシュは 2 倍、出力は 1.5 倍の単価を適用する。指示、ツール定義、履歴、ツール結果も入力総量に含める。ChatGPT ログインの allowance に同じ倍率が適用されるかは未確認。

## allowance（ChatGPT ログイン）

5 時間窓あたりの推定ローカルメッセージ数。実際の消費はタスク、モデル、設定で変わる。Pro の利用可能量とリセット時刻は Settings → Usage で確認する。

| モデル | Plus | Standard Business |
| --- | --- | --- |
| GPT-6.2 Astra | 6〜50 | 6〜50 |
| GPT-6.2 Sol | 17〜180 | 17〜180 |
| GPT-6 Luna | 350〜3,000 | 350〜3,000 |

同じタスクでのモデル間の allowance 消費比は未確認。Fast mode（`service_tier = "fast"`）は allowance を多く使う。

## GPT-6.2 の effort の指針

- モデルを切り替えたら、使い慣れたタスクで低い段から結果を確かめる
- 上げると allowance を多く使い、結果が良くなるとは限らない
- 足りない情報や権限は effort では補えない
- 段は allowance の固定量を決めるものではない

## 測り方

`claude-model-routing/references/notes.md` と同じ。設定ごとに別セッション、effort 以外はバイト単位で同一、難しい事例を混ぜる、繰り返し試行で確かめる。GPT-6.2 の曲線はまだ測っていない。
