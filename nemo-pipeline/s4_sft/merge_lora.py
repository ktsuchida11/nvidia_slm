"""SFTのLoRAアダプタ（PEFT形式）をbaseモデルへマージし、vLLM配信可能なHF形式で保存する。

NeMo-RL v0.6.0 (DTensor v2 + Automodel PEFT) のチェックポイントは
  step_N/policy/weights/model/adapter_model.safetensors + adapter_config.json
のPEFTアダプタ形式。LinearLoRA の forward は y = Wx + scale*B(Ax), scale=alpha/dim
なので、マージは W' = W + scale*(B@A)。

キー名は実装差（peftの base_model.model. プレフィックスや .default. セグメント）を
正規化して突き合わせ、1件でも不一致があれば失敗させる（黙って素通しさせない）。
"""
from __future__ import annotations
import argparse
import json
import pathlib
import shutil

import torch
from safetensors.torch import load_file
from transformers import AutoModelForCausalLM


def normalize(key: str) -> str:
    key = key.removeprefix("base_model.model.")
    key = key.replace(".default.", ".")
    return key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True, help="adapter_model.safetensors のあるディレクトリ")
    ap.add_argument("--base", required=True, help="baseモデル（HF名 or パス）")
    ap.add_argument("--tokenizer", required=True, help="チェックポイント同梱の tokenizer ディレクトリ")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    adapter_dir = pathlib.Path(a.adapter)
    cfg = json.loads((adapter_dir / "adapter_config.json").read_text())
    alpha = cfg.get("lora_alpha", cfg.get("alpha", 32))
    dim = cfg.get("r", cfg.get("dim", 16))
    scale = alpha / dim
    print(f"[merge] alpha={alpha} dim={dim} scale={scale}")

    print(f"[merge] loading base: {a.base}（CPU, bf16。RAM~19GB使用）")
    model = AutoModelForCausalLM.from_pretrained(
        a.base, dtype=torch.bfloat16, trust_remote_code=True, low_cpu_mem_usage=True)
    sd = model.state_dict()

    ad = load_file(str(adapter_dir / "adapter_model.safetensors"))
    a_keys = [k for k in ad if normalize(k).endswith(".lora_A.weight")]
    assert a_keys, f"lora_A キーが見つからない。実キー例: {list(ad)[:5]}"

    merged, misses = 0, []
    for ka in a_keys:
        prefix = normalize(ka)[: -len(".lora_A.weight")]
        kb = next((k for k in ad if normalize(k) == f"{prefix}.lora_B.weight"), None)
        kw = f"{prefix}.weight"
        if kb is None or kw not in sd:
            misses.append(prefix)
            continue
        W = sd[kw]
        W.data = (W.float() + scale * (ad[kb].float() @ ad[ka].float())).to(W.dtype)
        merged += 1
    assert not misses, f"baseと突き合わせできないLoRAモジュール: {misses[:5]} (計{len(misses)})"
    print(f"[merge] {merged} modules merged")

    out = pathlib.Path(a.out)
    model.save_pretrained(out, safe_serialization=True)
    # tokenizer はチェックポイント同梱のもの（chat_template.jinja 含む）を使用
    for f in pathlib.Path(a.tokenizer).iterdir():
        shutil.copy2(f, out / f.name)
    # trust_remote_code 用のモデル実装もアダプタディレクトリから同梱（閉域ロード可能に）
    for f in adapter_dir.glob("*.py"):
        shutil.copy2(f, out / f.name)
    print(f"[merge] saved -> {out}")


if __name__ == "__main__":
    main()
