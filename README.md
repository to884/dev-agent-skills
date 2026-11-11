# dev-agent-skills

Claude Code と Codex で使う Agent Skills をまとめたリポジトリです。

## スキル一覧

### タスクの委譲とモデル選択

| スキル | 使う場面 |
| --- | --- |
| [ask-claude](docs/skills/ask-claude.md) | Codex から Claude Code CLI に調査、レビュー、実装を依頼する |
| [ask-codex](docs/skills/ask-codex.md) | Claude Code から Codex CLI に調査、レビュー、実装を依頼する |
| [multi-agent-dispatch](docs/skills/multi-agent-dispatch.md) | 同じ課題を複数のモデルに独立して解かせ、結果を比較する |
| [claude-model-routing](docs/skills/claude-model-routing.md) | Claude のモデルと effort を選び、委譲するか会話のモデルを切り替えるかを判断する |
| [openai-model-routing](docs/skills/openai-model-routing.md) | Codex CLI に渡すモデルと reasoning effort を選ぶ |

### 調査と Git

| スキル | 使う場面 |
| --- | --- |
| [deep-dive](docs/skills/deep-dive.md) | 複数の問いを並列で Web 調査し、出典付きのレポートにまとめる |
| [git-conventions](docs/skills/git-conventions.md) | ブランチ名、コミットメッセージ、プルリクエストをリポジトリの規約にそろえる |

### 日本語文書と応答

| スキル | 使う場面 |
| --- | --- |
| [japanese-tech-writing](docs/skills/japanese-tech-writing.md) | 技術記事や解説の主張、因果関係、用語を整え、公開前に採点する |
| [natural-japanese](docs/skills/natural-japanese.md) | 議事録やレポート、記事などを読みやすく書く、直す、診断する |
| [i-have-adhd](docs/skills/i-have-adhd.md) | 次の行動、手順、進捗が見つけやすい応答に切り替える |

## インストール

このリポジトリで作業する場合、Codex と GitHub Copilot CLI は `.agents/skills/` からスキルを読み込みます。
Claude Code は `.claude/skills/` のシンボリックリンクから同じスキルを読み込みます。
Git がリンクを通常のファイルとして展開した場合は、下の手動配置で Claude Code のユーザー用ディレクトリにコピーできます。

### 別のプロジェクトに `npx skills` で入れる

Git と Node.js 22.20 以降が必要です。
スキルを使いたいプロジェクトのルートで実行します。
次の例は `git-conventions` を Codex、Claude Code、GitHub Copilot CLI 用にコピーします。

```sh
npx skills add to884/dev-agent-skills --skill git-conventions --agent codex claude-code github-copilot --copy
```

- 別のスキルを入れるときは `--skill` の値を一覧のスキル名に置き換えます。インストーラーが検出できるスキルは `npx skills add to884/dev-agent-skills --list` で確認できます。
- 使うエージェントだけを `--agent` に指定します。Cursor なら `cursor` を指定します。[対応エージェント一覧](https://github.com/vercel-labs/skills/blob/main/docs/agents.md)
- 複数のプロジェクトで使う場合は `--global` を付けて、ユーザー用ディレクトリに入れます。[インストーラーのオプション](https://github.com/vercel-labs/skills/blob/main/docs/commands.md#skills-add)

`npx skills` 1.7.6 は、リポジトリの `skills-lock.json` に記録された `natural-japanese` を一覧から除外します。
このリポジトリに含まれる版を使うには、clone したリポジトリの `.agents/skills/natural-japanese/` を、下の手順で配置してください。

### 手動で配置する

```sh
git clone https://github.com/to884/dev-agent-skills.git
```

clone したリポジトリの `.agents/skills/<スキル名>/` を、使いたいエージェントの配置先にフォルダーごとコピーします。
`SKILL.md` と一緒に、フォルダー内のスクリプトや参照資料もコピーしてください。
プロジェクト用の配置先は、スキルを使うプロジェクトのルートからの相対パスです。

| エージェント | プロジェクト用 | ユーザー用 | 読み込みの確認 |
| --- | --- | --- | --- |
| [Codex](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills) | `.agents/skills/` | `~/.agents/skills/` | CLI の `/skills` か、入力欄で `$` を使ってスキルを選ぶ |
| [Claude Code](https://code.claude.com/docs/en/skills#choose-where-skills-load) | `.claude/skills/` | `~/.claude/skills/` | 入力欄で `/git-conventions` のようにスキル名を指定する |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills) | `.agents/skills/` または `.github/skills/` | `~/.copilot/skills/` | セッション中に追加した場合は `/skills reload`。一覧は `/skills list` |
| [GitHub Copilot in VS Code](https://code.visualstudio.com/docs/agent-customization/agent-skills) | `.github/skills/` | `~/.copilot/skills/` | チャット欄に `/` を入力してスキルを選ぶ |

たとえば、VS Code の GitHub Copilot でプロジェクト用の `git-conventions` を使う場合は、`.github/skills/git-conventions/SKILL.md` ができるようにコピーします。

### Claudex で使う

BeamoTech の Claudex は、通常の GPT バックエンドのセッションで Codex と Claude Code のスキルを読み込みます。
上の表の Codex または Claude Code の配置先を使い、対象プロジェクトのルートで `claudex skills` を実行して、利用できるスキル名を確認します。[Claudex のスキルドキュメント](https://github.com/BeamoTech/Claudex/blob/main/docs/skills.md)

### スクリプトを使うスキル

インストールで追加されるのはスキルのファイルです。
Python、`uv`、委譲先 CLI のインストールやログインなど、実行に必要な準備は各スキルの詳細ページを確認してください。
実行例の `.agents/skills/...` と異なる場所にインストールした場合は、コマンド内のスクリプトのパスを実際の配置先に置き換えます。
