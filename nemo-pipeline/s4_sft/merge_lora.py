"""LoRA(lora_A/lora_B)をbase重みへマージしてHFチェックポイントを完成させる。

NeMo-RL v0.6.0 の convert_dcp_to_hf.py はLoRAを処理しない（素通し変換）ため、
出力の pytorch_model.bin には PatchedLinearLoRA の lora_A/lora_B キーが残り、
base重みは学習前のまま = そのまま配信すると学習結果が反映されない。

Automodel の LinearLoRA 実装（components/_peft/lora.py）より:
  forward: y = Wx + scale * lora_B(lora_A(x)),  scale = alpha / dim
  → マージ: W' = W + scale * (B @ A)
"""
from __future__ import annotations
import argparse
import pathlib

import torch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hf-dir", required=True, help="convert_dcp_to_hf.py の出力ディレクトリ")
    ap.add_argument("--alpha", type=float, default=32.0, help="lora_cfg.alpha（sft_lora.yaml準拠）")
    ap.add_argument("--dim", type=float, default=16.0, help="lora_cfg.dim（sft_lora.yaml準拠）")
    a = ap.parse_args()

    path = pathlib.Path(a.hf_dir) / "pytorch_model.bin"
    sd = torch.load(path, map_location="cpu", weights_only=True)
    scale = a.alpha / a.dim

    merged = 0
    for ka in [k for k in list(sd) if k.endswith(".lora_A.weight")]:
        prefix = ka[: -len(".lora_A.weight")]
        kb, kw = f"{prefix}.lora_B.weight", f"{prefix}.weight"
        A, B, W = sd.pop(ka), sd.pop(kb), sd[kw]
        sd[kw] = (W.float() + scale * (B.float() @ A.float())).to(W.dtype)
        merged += 1

    assert merged > 0, "lora_A/lora_B が見つからない（LoRA無効で学習した？変換元を確認）"
    leftover = [k for k in sd if "lora" in k.lower()]
    assert not leftover, f"未処理のloraキーが残存: {leftover[:5]}"

    torch.save(sd, path)
    print(f"[merge] {merged} modules merged (scale={scale}) -> {path}")


if __name__ == "__main__":
    main()
