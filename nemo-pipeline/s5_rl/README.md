# S5 強化学習 GRPO（NeMo-RL）
報酬= `common/reward.py`（検証可能: source_exists/citation + penalty(空応答・反復=EOS崩壊検知)）。
評価セット(heldout)と報酬は分離（reward hacking検出のため）。
実行: `make grpo`。**まず4B・1GPUで配管検証**。9Bは (a)ローカル64GB=ギリギリ可（yaml内の64GBプロファイル＝絞りノブ適用、OOMならUnslothルート） (b)借りGPU 4×L40S=余裕、のどちらか。
設定骨子= grpo_qwen.yaml。実キーは https://github.com/NVIDIA-NeMo/RL の examples/configs を正とする。
チェックポイントは外部退避（spot中断前提）。K8s不要（単一ノードDocker）。
