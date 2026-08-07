"""S3 DAPT 学習本体（loop10 P3/P4・nemo:25.04 コンテナ内で torchrun 実行）。

NeMo 2.3 の AutoModel 経路（examples/llm/sft/automodel.py r2.3.0 準拠）を使う:
  - Nemotron-Nano-9B-v2-Japanese はハイブリッド(Mamba+Attention)アーキテクチャのため
    Megatron 変換(import_ckpt)ではなく HF Transformers バックエンド(HFAutoModelForCausalLM)で学習する
  - DAPT = 全トークン loss の継続事前学習。corpus_train.jsonl をトークナイズ→連結→seq_len 分割
  - 分散は FSDP2（devices=DP）。9B full-param が載らない場合は train.cpu_offload / train.peft: lora へ

実行（run_dapt.sh 経由が正）:
  torchrun --nproc-per-node=4 dapt_train.py --config /pipeline/s3_pretraining/dapt.yaml
"""
from __future__ import annotations
import argparse, os

import yaml


def load_cfg(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_lm_dataset(train_path: str, val_path: str, tokenizer, seq_len: int):
    """jsonl({"text":...}) → トークナイズ→連結→seq_len チャンク（標準の group_texts 方式）"""
    from datasets import DatasetDict, load_dataset

    raw = load_dataset("json", data_files={"train": train_path, "validation": val_path})
    tok = getattr(tokenizer, "tokenizer", tokenizer)          # NeMoラッパを剥がす
    eos = tok.eos_token_id if tok.eos_token_id is not None else 0

    def tokenize(batch):
        return {"ids": [tok(t, add_special_tokens=False)["input_ids"] + [eos]
                        for t in batch["text"]]}

    def chunk(batch):
        flat = [i for ids in batch["ids"] for i in ids]
        total = (len(flat) // seq_len) * seq_len
        chunks = [flat[i:i + seq_len] for i in range(0, total, seq_len)]
        return {
            "input_ids": chunks,
            "labels": [c[1:] + [eos] for c in chunks],
            "loss_mask": [[1] * seq_len for _ in chunks],     # DAPT: 全トークンで学習
        }

    cols = raw["train"].column_names
    out = {}
    for split in ("train", "validation"):
        ds = raw[split].map(tokenize, batched=True, remove_columns=cols)
        out[split] = ds.map(chunk, batched=True, batch_size=64, remove_columns=["ids"])
    return DatasetDict(out), eos


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/pipeline/s3_pretraining/dapt.yaml")
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    m, t, d = cfg["model"], cfg["train"], cfg["data"]

    import fiddle as fdl
    from nemo import lightning as nl
    from nemo.automodel.loss import masked_cross_entropy
    from nemo.collections import llm
    from nemo.collections.llm.recipes.optim.adam import pytorch_adam_with_cosine_annealing

    model = llm.HFAutoModelForCausalLM(
        model_name=m["name"],
        attn_implementation=m.get("attn_implementation", "sdpa"),
        loss_fn=masked_cross_entropy,
        trust_remote_code=bool(m.get("trust_remote_code", True)),
        enable_grad_ckpt=bool(m.get("enable_grad_ckpt", True)),
    )

    devices = int(t.get("devices", 4))
    offload_policy = None
    if t.get("cpu_offload"):
        from nemo.lightning.pytorch.strategies.fsdp2_strategy import (HAS_CPU_OFFLOAD_POLICY,
                                                                      CPUOffloadPolicy)
        assert HAS_CPU_OFFLOAD_POLICY, "CPUOffloadPolicy が import できない"
        offload_policy = CPUOffloadPolicy()
    strategy = nl.FSDP2Strategy(
        data_parallel_size=int(t.get("dp_size", devices)),
        tensor_parallel_size=int(t.get("tp_size", 1)),
        context_parallel_size=int(t.get("cp_size", 1)),
        checkpoint_io=model.make_checkpoint_io(adapter_only=t.get("peft") == "lora"),
        offload_policy=offload_policy,
    )

    peft = None
    if t.get("peft") == "lora":
        peft = llm.peft.LoRA(dim=int(t.get("lora_r", 64)),
                             alpha=int(t.get("lora_alpha", 2 * int(t.get("lora_r", 64)))))

    dataset, eos = build_lm_dataset(d["train"], d["val"], model.tokenizer, int(t["seq_len"]))
    datamodule = llm.HFDatasetDataModule(
        dataset,
        split=["train", "validation"],
        micro_batch_size=int(t.get("micro_batch", 1)),
        pad_token_id=eos,
    )

    loggers = []
    if os.environ.get("MLFLOW_TRACKING_URI"):
        try:
            from lightning.pytorch.loggers import MLFlowLogger
            loggers.append(MLFlowLogger(experiment_name="dapt",
                                        tracking_uri=os.environ["MLFLOW_TRACKING_URI"]))
        except (ImportError, ModuleNotFoundError) as e:
            # nemo:25.04 は mlflow 非同梱。追跡は落として学習は続行（lossはstdout+ckptログに残る）
            print(f"⚠ MLflow logger 無効（{e}）— 追跡なしで続行")

    max_steps = int(t["max_steps"])
    log = nl.NeMoLogger(
        name="dapt",
        log_dir=cfg.get("ckpt_dir", "/ckpt/dapt"),
        use_datetime_version=False,
        ckpt=nl.ModelCheckpoint(save_last=True,
                                every_n_train_steps=int(t.get("save_every", max(max_steps // 2, 1))),
                                monitor="reduced_train_loss", save_top_k=1,
                                save_on_train_epoch_end=True, save_optim_on_train_end=True),
        wandb=None,
    )

    llm.api.finetune(
        model=model,
        data=datamodule,
        trainer=nl.Trainer(
            devices=devices,
            num_nodes=1,
            max_steps=max_steps,
            accelerator="gpu",
            strategy=strategy,
            log_every_n_steps=int(t.get("log_every", 1)),
            num_sanity_val_steps=0,
            val_check_interval=int(t.get("val_check_interval", max(max_steps // 2, 1))),
            limit_val_batches=int(t.get("limit_val_batches", 50)),
            accumulate_grad_batches=int(t.get("accumulate_grad_batches", 1)),
            gradient_clip_val=float(t.get("grad_clip", 1.0)),
            use_distributed_sampler=True,
            logger=loggers or None,
            precision="bf16-mixed",
        ),
        optim=fdl.build(pytorch_adam_with_cosine_annealing(max_lr=float(t["lr"]), foreach=False)),
        peft=peft,
        log=log,
        resume=None,     # 自動レジューム無効（旧stepの誤読込罠=gpu-infraの教訓。再開はckpt退避後に明示指定）
    )


if __name__ == "__main__":
    main()
