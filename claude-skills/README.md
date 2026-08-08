# Claude Code スキル

## nemo-pipeline-runner

NeMoパイプライン一式（データ→S1→S2→S3 DAPT→S4 SFT→S5 GRPO→S6評価）をClaude Codeに
自動運転させ、**S6の合否と診断定石で次の一手を判断する評価ループ**を回すスキル。
loop1〜10の実測知見を references/ に内蔵（承認ゲート・リモートGPU運用つき）。

### 構成

```
nemo-pipeline-runner/
  SKILL.md                      # 本体: ループの型・承認ゲート・フェーズフロー
  references/
    decision-table.md           # SFT/GRPO/DAPT の適用可否（タスク×データ×base強度）
    evaluation.md               # 評価3層・リーク検査・統計的判定・GRPO診断
    billing-gates.md            # 課金承認ゲート（概算→実測→再見積り→承認）と実績
    traps.md                    # 実機罠カタログ（NeMo-RL / NeMo AutoModel / vLLM / Curator）
    operations.md               # リモートGPUノード運用（S3同期・ckpt退避・停止・PR規律）
```

### インストール

```bash
# プロジェクト単位（推奨: このリポで作業する時だけ有効）
mkdir -p .claude/skills
cp -r claude-skills/nemo-pipeline-runner .claude/skills/

# またはユーザー全体
cp -r claude-skills/nemo-pipeline-runner ~/.claude/skills/
```

### 使い方（Claude Codeでの発話例）

- 「次の学習ループを設計して」（前ループ総括の示唆から計画→承認→実行）
- 「このタスクはSFT/GRPO/DAPTどれを使うべき？」（意思決定表で判断）
- 「base測定まで進めて、結果を要約して」
- 「GRPOの結果がフラット。診断して」（$0診断: reward推移・fix/break差分）
- 「ノードで学習して」（承認ゲート→同期→tmux実行→停止確認まで）

### 設計方針

- 実行は必ず `make` 経由（Makefileが唯一の入口）
- 課金・GPU占有・外部API送信は**必ず人間の承認**を挟む（概算→実測→再見積りの2段ゲート）
- held-out不可侵・リーク検査must・「1周1変更」・判断と実測値を docs/loop-XX-report.md に記録
- 罠を踏んだら references/traps.md へ追記してカタログを育てる
