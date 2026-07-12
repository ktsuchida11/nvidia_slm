# S0 評価設計（最初にやる）
目的: 「使える」を数値で先に定義。**学習より先に** 指標・閾値・heldoutを固定し、baseを測る。
- 指標と閾値 → `../s6_evaluation/eval.yaml`（schema=100% / analysis_match≥0.85 / source_exists=100% / citation≥0.95）
- heldout → S2が生成（`/data/distilled/heldout.jsonl`）。学習・GRPO報酬に**絶対に使わない**。
- base測定 → ランブックStep 4（`make eval TAG=base`）。この数字が超えるべき線。
K8s不要。前回の失敗（評価を最後に回しVal lossに騙された）の再発防止がこのステージの存在理由。
