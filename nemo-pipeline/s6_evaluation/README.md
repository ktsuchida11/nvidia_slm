# S6 評価 — 実装済（合否をexit codeで返す）
heldoutをOpenAI互換エンドポイントに流し、analysis(schema/sectors/query_type/date_range一致)と
generation(source_exists/citation)を集計、eval.yamlの閾値で合否判定（0=合格/2=不合格→前段へ）。
```bash
make eval-dry                                        # 配管検証（全満点になるはず）
OPENAI_BASE_URL=http://<推論>/v1 make eval TAG=base  # base測定（S0の締め）
... TAG=sft / TAG=grpo                               # 同一heldoutで横並び比較 → results/eval_*.json
```
参考ベンチ(japanese_lm_fin_harness等)はNeMo Evaluator/lm-evalで別途（学習に使わない）。K8s不要。
