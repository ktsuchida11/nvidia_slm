"""SFTのLoRAアダプタ（PEFT形式）をbaseモデルの重みへマージし、vLLM配信可能なHF形式で保存する。

モデルはインスタンス化しない（Nemotron-Hのmodeling実装は mamba_ssm 等のGPU向け依存を
要求するため）。baseのsafetensorsシャードを直接読み、該当テンソルにデルタを加算して
書き出す。RAM使用もシャード単位（~5GB）で済む。

LinearLoRA の forward は y = Wx + scale*B(Ax), scale=alpha/dim
→ マージ: W' = W + scale*(B@A)
"""
from __future__ import annotations
import argparse
import json
import pathlib
import shutil

from huggingface_hub import snapshot_download
from safetensors.torch import load_file, save_file


def normalize(key: str) -> str:
    key = key.removeprefix("base_model.model.")
    key = key.replace(".default.", ".")
    return key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True, help="adapter_model.safetensors のあるディレクトリ")
    ap.add_argument("--base", required=True, help="baseモデル（HF名。キャッシュ利用）")
    ap.add_argument("--tokenizer", required=True, help="チェックポイント同梱の tokenizer ディレクトリ")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    adapter_dir = pathlib.Path(a.adapter)
    cfg = json.loads((adapter_dir / "adapter_config.json").read_text())
    alpha = cfg.get("lora_alpha", cfg.get("alpha", 32))
    dim = cfg.get("r", cfg.get("dim", 16))
    scale = alpha / dim
    print(f"[merge] alpha={alpha} dim={dim} scale={scale}")

    # LoRAデルタ表: 対象weightキー -> (A, B)
    ad = load_file(str(adapter_dir / "adapter_model.safetensors"))
    deltas = {}
    for ka in [k for k in ad if normalize(k).endswith(".lora_A.weight")]:
        prefix = normalize(ka)[: -len(".lora_A.weight")]
        kb = next((k for k in ad if normalize(k) == f"{prefix}.lora_B.weight"), None)
        assert kb is not None, f"lora_B が見つからない: {prefix}"
        deltas[f"{prefix}.weight"] = (ad[ka], ad[kb])
    assert deltas, f"lora_A キーが見つからない。実キー例: {list(ad)[:5]}"
    print(f"[merge] adapter modules: {len(deltas)}")

    print(f"[merge] resolving base snapshot: {a.base}")
    snap = pathlib.Path(snapshot_download(
        a.base,
        allow_patterns=["*.safetensors", "*.safetensors.index.json", "config.json",
                        "generation_config.json", "*.py"]))

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    merged = set()
    for shard in sorted(snap.glob("*.safetensors")):
        tensors = load_file(str(shard))
        hit = 0
        for kw in list(tensors):
            if kw in deltas:
                A, B = deltas[kw]
                W = tensors[kw]
                tensors[kw] = (W.float() + scale * (B.float() @ A.float())).to(W.dtype)
                merged.add(kw)
                hit += 1
        save_file(tensors, str(out / shard.name), metadata={"format": "pt"})
        print(f"[merge] {shard.name}: {hit} tensors merged")

    missing = set(deltas) - merged
    assert not missing, (
        f"baseに存在しないLoRA対象キー: {sorted(missing)[:5]} (計{len(missing)})。"
        "アダプタとbaseのキー命名の突き合わせを確認すること")

    # 付帯ファイル: config/index/モデル実装は base スナップショットから、
    # tokenizer はチェックポイント同梱（chat_template.jinja 含む）を使用
    for f in list(snap.glob("*.json")) + list(snap.glob("*.py")):
        if f.name.endswith(".safetensors.index.json") or f.name in ("config.json", "generation_config.json") or f.suffix == ".py":
            shutil.copy2(f, out / f.name)
    for f in pathlib.Path(a.tokenizer).iterdir():
        shutil.copy2(f, out / f.name)

    # config.json に torch_dtype を必ず書く。base(Nemotron)のconfigにはこのキーが無く、
    # transformers/automodelはfp32デフォルトで実体化する → 9BがGPUで33GB(bf16の倍)になり
    # GRPO refitのGPU OOMとserve-sftの無駄なfp32配信を招いた(ループ8実機で特定)。
    # 実テンソルのdtypeはシャード先頭から検出する(常にbaseと同一 = 通常bf16)
    cfg_path = out / "config.json"
    cfg_json = json.loads(cfg_path.read_text())
    if not cfg_json.get("torch_dtype"):
        first_shard = sorted(out.glob("*.safetensors"))[0]
        dtype = str(next(iter(load_file(str(first_shard)).values())).dtype).removeprefix("torch.")
        cfg_json["torch_dtype"] = dtype
        cfg_path.write_text(json.dumps(cfg_json, indent=2))
        print(f"[merge] config.json に torch_dtype={dtype} を補完")
    print(f"[merge] {len(merged)} modules merged / saved -> {out}")


if __name__ == "__main__":
    main()
