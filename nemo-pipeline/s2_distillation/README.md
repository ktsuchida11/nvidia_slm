# S2 蒸留・メタデータ — 実装済
入力: `/data/curated` → 出力: `/data/distilled/{train,valid,heldout}.jsonl + stats.json`
処理: カテゴリ層別の合成問合せ → 教師(claude-sonnet-4-6)ラベル → スキーマ厳密検証(common/schema) →
生成タスクは**忠実性ゲート(source_exists=S5報酬と同一関数)** → dedup → seed固定の層別分割。
```bash
make distill-dry                       # API不要の配管検証（TS-04）
make distill N_ANALYSIS=300 N_GEN=100  # 本番（要ANTHROPIC_API_KEY。概算コストを先に表示）
```
heldoutは評価専用。K8s不要（CPUコンテナ+API）。
