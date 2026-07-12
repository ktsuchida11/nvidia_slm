# S4 事後学習 SFT/LoRA
S2のtrain/validでSFT（設定骨子= sft_lora.yaml。実キーはNeMo Frameworkのfinetuneレシピを正とする）。
実行: **ローカルGPU 64GBなら快適に完結**（9B LoRA ~24-32GB）。無ければ借りGPU（4B=1×L40S〜 / 9B=1×A100〜）。どちらも同じ `make sft`。
**終わったら即 `make eval TAG=sft`**（base比を確認してからS5へ）。
軽量代替: Unsloth(Colab)でも同じtrain.jsonlが使える（同一heldoutで両路線比較可）。K8s不要。
