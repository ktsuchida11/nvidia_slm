# Claude Code スキル

## nemo-pipeline-runner
NeMoパイプライン一式（環境確認→データ→S1→S2→学習→S6評価→S7）をClaude Codeに自動運転させ、
**S6の合否で前段へ戻る評価ループ**を回すスキル。承認ゲート（課金・GPU・機微画像）つき。

### インストール
```bash
# プロジェクト単位（推奨: このリポで作業する時だけ有効）
mkdir -p .claude/skills
cp -r claude-skills/nemo-pipeline-runner .claude/skills/

# またはユーザー全体
cp -r claude-skills/nemo-pipeline-runner ~/.claude/skills/
```

### 使い方（Claude Codeでの発話例）
- 「NeMoパイプラインを一周して。GPUなしでできるところまで」
- 「base測定まで進めて、eval_base.jsonを要約して」
- 「SFTを回して、baseと比較して次の一手を提案して」
- 「評価ループを回して。閾値未達なら分岐表に従って1手だけ変えて」
- 「ガードレールをgarakで検証してbefore/afterを報告して」

### 設計方針
- 実行は必ず `make` 経由（Makefileが唯一の入口）
- 課金・GPU占有・外部API送信は**必ず人間の承認**を挟む
- held-out不可侵・「1周1変更」・判断履歴を results/loop_report.md に記録
