---
name: openai-model-routing
description: Codex CLI に渡す OpenAI のモデルを GPT-6 Astra、GPT-6 Sol、GPT-6 Luna（と GPT-5.6 Sol）から選ぶときと、reasoning effort の段を決めるときに使う。各モデルを選ぶ条件と単価、ChatGPT ログインの共有 allowance と API 従量の違い、ask-codex と multi-agent-dispatch での指定方法、モデルを変えたときのキャッシュの扱いを提供する。Codex へ委譲する前、複数モデルで比較する前、effort をどの段にするか決めるときに参照する。
---

# OpenAI モデルの選び分け

既定は GPT-6 Astra の `medium`。作者の `~/.codex/config.toml` と OpenAI の指針に合わせた値。
このスキルは、既定から外す理由があるかの判定と、外すときの指定方法を持つ。
Claude 側のモデル選択は `claude-model-routing`。判定の軸は同じで、候補と経路と課金の形が違う。

## 使う場面

- Codex へ 1 つのタスクを委譲する前（`ask-codex`）
- 複数モデルに同じタスクを解かせる前（`multi-agent-dispatch`）。どの記号を選ぶか
- 判断が薄く量だけが多い作業を Codex に流す前。成果を機械的に検算できるものに限る
- 作者が「Codex はどのモデルで走らせるか」と尋ねたとき

「割り当て」を上から見て Astra に落ちるなら、何も言わずそのまま進む。
作者がすでにモデルか effort を指定しているなら、判断を求められない限り触れない。

## 制約 1：GPT を使う手は Codex CLI への委譲だけ

Claude Code のセッションは GPT にならない。GPT を使うのは Codex CLI（`codex exec`）を子として起こすときで、経路は 2 つ。

1. **ask-codex**：1 つのタスクを 1 モデルに渡し、最終メッセージを受け取る
2. **multi-agent-dispatch**：同じタスクを Claude と Codex の最大 4 体に渡し、成果を集める

作者が Codex を直接使っている場面でこちらにできるのは提案だけ。切り替えは作者が Codex 側の `/model` か `config.toml` で行う。

## 制約 2：課金の形が 2 つある

| 認証 | 課金 | この環境 |
| --- | --- | --- |
| ChatGPT ログイン | プランに含まれる Work と Codex の共有 allowance。5 時間窓と週窓の両方に残りが要る | こちら。`codex login status` が `Logged in using ChatGPT` |
| API キー | トークン単価の従量 | 使っていない |

allowance の消費はモデルと effort で変わる。同じタスクで Astra は GPT-5.6 Sol の約 2 倍、GPT-5.6 Luna の約 50 倍の allowance を使う（5 時間あたりの推定メッセージ数の比。`references/notes.md`）。
モデルを切り替えても allowance は戻らない。作者のプランと残量は未確認なので、長い委譲や dispatch の `all` の前に Settings → Usage を見てもらう。

単価表は API の値。ChatGPT ログインでは費用の桁の見当にだけ使い、作者への費用の説明は allowance の消費で言う。

## 制約 3：モデルを変えるとキャッシュは再利用できない

OpenAI のプロンプトキャッシュもモデルごとに別で、切り替えると引き継げない。TTL は 30 分。
Codex は `exec resume` で同じセッションを続けられる（`ask-codex` の `--resume`、`dispatch` のレビュー段階）。文脈は残るが、キャッシュは 30 分空くと切れる。
入力が 272K トークンを超えると、そのリクエスト全体が入力とキャッシュ 2 倍、出力 1.5 倍になる。委譲する材料は 272K 未満に収める。

## 判定と割り当て

軸は `claude-model-routing` と同じ 4 つ。

1. **難度**：正解までが一本道か。設計の選択や仮説の切り分けを要するか
2. **持ち越すステップ**：判断と制約と依存関係を保持し続けるステップ数
3. **訂正の費用**：検査のあとに残る分で測る
4. **判断の薄さ**：読む量と書く量に対して下す判断が少ないか。軽いモデル（Luna、Sol）を選ぶ方向にだけ使う

上から見て、最初に当てはまった行で決める。条件はモデルの能力ではなく割り当ての条件。

| 順 | モデル | ID | `ask-codex --model` | `dispatch` 記号 | $/1M 入力・出力 | 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | GPT-6 Luna | `gpt-6-luna` | `gpt-6-luna` | 無し | 0.1・0.5 | 判断が薄く量だけが多い。受入条件が確定し、成果を機械的に検査できる。材料が 272K に収まる |
| 2 | GPT-6 Sol | `gpt-6-sol` | `gpt-6-sol` | 無し | 2・10 | 手順が決まっていて既存のパターンを踏襲する。受入条件が確定し、成果を機械的に検査できる |
| 3 | GPT-6 Astra | `gpt-6-astra` | `astra`（既定） | D | 10・50 | 既定。上のどれにも当てはまらない |

文脈窓は 3 つとも 1,050,000、最大出力 128,000。Astra の上の段は無く、上げる手は effort。
GPT-5.6 Sol（`gpt-5.6-sol`、`--model sol`、記号 C、$4・$20）はスクリプトが持つ前世代。GPT-6 Sol のほうが安く新しいので、5.6 を選ぶ理由は「dispatch の C として設定済み」以外に無い。置き換えるかは作者の決定。

Astra について OpenAI が書いていること。

- `none` を受け付けない（400）。最低は `low`
- 完了と見なす前にテストを徹底する傾向がある。小さな変更では、期待するテストの範囲を prompt に書く
- 出力トークンが少なく、タスクあたりの費用は前世代より安いことがある。単価だけで判断しない

## effort の決め方

effort はモデルより先に動かす。OpenAI 自身が「Astra の `low` が Sol の `high` を上回ることがある。Sol の `high` で満足なら Astra の `low` か `medium` から」と書いている。

| 段 | OpenAI の用途 | Astra |
| --- | --- | --- |
| `none` | 推論が要らない遅延重視。分類、短い取得 | 不可 |
| `low` | 道具呼びと多段の判断を速く安く | 可 |
| `medium` | 既定。計画と複雑な推論の釣り合い | 既定 |
| `high` | 難しい推論、複雑なデバッグ、深い計画 | 可 |
| `xhigh` | 深い調査、長く走る agentic な作業 | 可 |
| `max` | 最難の作業 | 可 |
| `ultra` | サブエージェントへ委任する形。Astra と Sol | `codex exec` で通るかは未確認 |

Codex CLI では `-c model_reasoning_effort="<段>"`。`ask-codex` と `dispatch` の `--effort` は `medium|high|xhigh` に絞ってあり、既定は `high`。`low` や `max` を使うにはスクリプトの `EFFORTS` を広げるか、`codex exec` を直接呼ぶ。
`ask-codex` の既定 `high` と `config.toml` の `medium` は食い違っている。どちらに揃えるかは作者の決定。

段ごとの計測値は無い。Anthropic の曲線（`claude-model-routing`）を GPT に当てはめない。OpenAI が書いているのは次の 3 つだけ。

- 上げると allowance を多く使い、結果が良くなるとは限らない
- 足りない情報や権限は effort では補えない。結果に抜けがあるなら、上げる前に指示と材料を確かめる
- 段は allowance の固定量を決めるものではない

当たりが付かないときは `medium`。委譲前に決め、結果を見てから上げる。`xhigh` 以上は allowance の残りを見てから。

## 経路 1：ask-codex

```powershell
python .agents/skills/ask-codex/scripts/ask_codex.py ask --task-file <path> --model astra --effort medium
```

`--model` は `astra`、`sol`（GPT-5.6 Sol）、またはモデル ID そのもの（`gpt-6-sol`、`gpt-6-luna`）。
委譲の条件と prompt に書くものは `claude-model-routing` の経路 1 と同じ。Codex 向けに次を足す。

- テストの範囲。書かないと Astra は徹底的に検証する
- `--mode`。読むだけなら `analysis`、書かせるなら `edit`
- 材料の総量。272K を超えるなら分ける

`high` 以上、調査、実装は `--detach` で切り離し、`wait` で受け取る。

## 経路 2：multi-agent-dispatch

記号 C（GPT-5.6 Sol）と D（GPT-6 Astra）。`--agents` で絞る。`run.json` の `model` を書き換えれば GPT-6 Sol や Luna も使える。
`--effort` は全員に同じ段がかかる。GPT だけ変えるなら `run.json` の各エージェントの `effort` を段階ごとに書く。

## 提案の書き方

作者に Codex 側の切り替えを薦めるときは、`claude-model-routing` の経路 2 と同じ 4 点を 1 段落で書く。費用は allowance の消費で言い、API 単価で言わない。

## してはいけないこと

- Astra に `none` を指定する
- Anthropic の effort 計測値を GPT に当てはめて書く
- API 単価を ChatGPT ログインの費用として作者に伝える
- 272K を超える材料をそのまま委譲する
- allowance の残りを確かめずに `xhigh` 以上や dispatch の `all` を薦める
- `ultra` を確かめずに使えるものとして書く
- allowance を戻す目的でモデルを切り替える提案をする
- 確認していない特性や単価を推測で書く
- 作者の判断が要る作業を委譲する

## 典拠と実測値

単価、段、allowance の推定値とその典拠は `references/notes.md`。
OpenAI がモデルを増減したとき、Codex を更新したときは同ファイルの確認日を更新する。単価の典拠は developers.openai.com のモデルページ。

## 関連

- `claude-model-routing`：判定の軸の定義と委譲の条件はこちらが持つ
- `ask-codex`、`multi-agent-dispatch`：経路
