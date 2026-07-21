# S5 強化学習 GRPO（NeMo-RL）
報酬= `common/reward.py`（検証可能）:
- generation: source_exists/citation + penalty(空応答・反復=EOS崩壊検知)
- analysis: schema + sectors/query_type一致 + **date_range一致(最重み0.5)** + penalty（ループ8。
  SFTで上書きできない暦計算・日付規則を直接罰する）

評価セット(heldout)と報酬は分離（reward hacking検出のため）。

**実装済みの構成**（キー構造は NVIDIA-NeMo/RL examples/configs/grpo_math_{1B,8B}.yaml 準拠・2026-07時点）:
- `finance_env.py` — カスタム報酬環境 `finance_grounding`（EnvironmentInterface実装。reasoningトレース除去→ground_truthのキーでreward_generation/reward_analysisへ振り分け。純関数部分は単体テスト済み）
- `run_grpo_finance.py` — `register_env()` で環境登録後に examples/run_grpo.py へ委譲する起動ラッパ
- `prep_rl_data.py` — distilled → ResponseDataset形式（generation: chunk_labels / analysis: ゴールドラベルを格納）
- `grpo_qwen.yaml` — generation用 48GB×1既定プロファイル（LoRA・seq2048・gpu_memory_utilization 0.4 等の絞りノブ適用済み）
- `grpo_analysis.yaml` — analysis用（`make grpo GRPO_CFG=grpo_analysis.yaml`。小規模プローブ: 40step・LABEL_SYS・max_new_tokens 400）

手順: `make prep-rl` → `make grpo`（analysisは `GRPO_CFG=grpo_analysis.yaml`）→ `make convert-grpo` → `make serve-grpo` → `make eval TAG=grpo`。**まず4B・1GPUで配管・報酬検証**（Route 48GB）。
9B GRPOは48GB単体では不足（~50-60GB）→ (a)Unslothルート(~20-25GB) (b)借りGPU 4×L40S。
★実機確認事項: `RL_IMG` のタグ整合 / ResponseDataset→metadata(ground_truth)の受け渡し /
Nemotron-H(Mamba)直GRPO時は vLLM>=0.17 か `grpo.seq_logprob_error_threshold: 2`（NeMo-RL nemotron-3-nanoガイド）。
チェックポイントは外部退避（spot中断前提）。K8s不要（単一ノードDocker）。
