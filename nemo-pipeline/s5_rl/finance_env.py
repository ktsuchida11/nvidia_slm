"""S5 GRPO カスタム報酬環境 — common/reward.py の検証可能報酬を NeMo-RL に組み込む。

インターフェースは NeMo-RL nemo_rl/environments/code_jaccard_environment.py と
interfaces.EnvironmentReturn（2026-07時点 main）を正として実装。
登録は run_grpo_finance.py が register_env("finance_grounding", ...) で行う。

データ契約（prep_rl_data.py が生成する grpo_*_train.jsonl。中身のキーでタスクを判別）:
  generation: metadata["ground_truth"] = '{"chunk_labels": ["sample.jsonl#0", ...]}' (JSON文字列)
  analysis:   metadata["ground_truth"] = '{"label": {...ゴールドラベルJSON...}}' (JSON文字列)

報酬（common/reward.py と同一関数）:
  generation = 0.5*source_exists + 0.2*citation_format + penalty
  analysis   = 0.2*schema + 0.15*sectors + 0.15*query_type + 0.5*date_range + penalty（ループ8）
Nemotron系のreasoningトレース(<think>...</think>)は採点前に除去する（docs/06の作法）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, TypedDict

_PIPELINE_DIR = os.environ.get("PIPELINE_DIR", "/pipeline")
sys.path.insert(0, os.path.join(_PIPELINE_DIR, "common"))
from reward import reward_analysis, reward_generation  # noqa: E402  (common/reward.py)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """reasoningトレースを除去し最終回答のみを採点対象にする（閉じタグ欠落にも対応）。"""
    out = _THINK_RE.sub("", text)
    if "<think>" in out:            # 閉じタグ無し=生成が途中で切れた場合は全体を空扱い
        out = out.split("<think>", 1)[0]
    return out.strip()


def score_batch(responses: list[str], ground_truths: list[str]) -> list[float]:
    """純関数部分（ray/nemo_rl 無しで単体テスト可能）。gtのキーでタスクを判別する。"""
    scores = []
    for ans, gt in zip(responses, ground_truths):
        try:
            obj = json.loads(gt)
        except Exception:
            obj = {}
        ans = strip_reasoning(ans)
        if "label" in obj:
            scores.append(float(reward_analysis(ans, obj["label"])))
        else:
            scores.append(float(reward_generation(ans, obj.get("chunk_labels", []))))
    return scores


# --- ここから NeMo-RL ランタイム依存（GPUホストのRL環境内でのみ import 可能） -----
try:
    import ray
    import torch
    from nemo_rl.data.interfaces import LLMMessageLogType
    from nemo_rl.environments.interfaces import EnvironmentInterface, EnvironmentReturn

    class FinanceEnvConfig(TypedDict):
        num_workers: int

    class FinanceEnvMetadata(TypedDict):
        ground_truth: str

    @ray.remote(max_restarts=-1, max_task_retries=-1)
    class FinanceGroundingEnvironment(EnvironmentInterface[FinanceEnvMetadata]):
        """検証可能報酬環境（1ターン）。generation=出典グラウンディング / analysis=ラベル一致。"""

        def __init__(self, cfg: FinanceEnvConfig):
            self.cfg = cfg  # 報酬は正規表現のみで軽量のためワーカー分散は不要

        def shutdown(self) -> None:
            pass

        def step(
            self,
            message_log_batch: list[LLMMessageLogType],
            metadata: list[FinanceEnvMetadata],
            return_extracted_answer: bool = False,
        ) -> EnvironmentReturn[FinanceEnvMetadata]:
            responses = []
            for conv in message_log_batch:
                responses.append("".join(
                    str(m["content"]) for m in conv if m["role"] == "assistant"))
            gts = [m["ground_truth"] for m in metadata]
            scores = score_batch(responses, gts)
            observations = [
                {"role": "environment",
                 "content": f"reward={s:.2f} (verifiable)"} for s in scores
            ]
            answers = ([strip_reasoning(r) for r in responses]
                       if return_extracted_answer else None)
            return EnvironmentReturn(
                observations=observations,
                metadata=metadata,
                next_stop_strings=[None] * len(responses),
                rewards=torch.tensor(scores, dtype=torch.float32),
                terminateds=torch.ones(len(responses), dtype=torch.bool),
                answers=answers,
            )

        def global_post_process_and_metrics(
            self, batch: Any
        ) -> tuple[Any, dict[str, float | int]]:
            """全rolloutバッチのメトリクス集計（v0.6.0 EnvironmentInterface の抽象メソッド。
            未実装だと ray.remote のActor化が TypeError で失敗する — 実機ループ8で発現）。
            集計流儀は同版 math_environment.py を踏襲: 途中切断(is_end=0)は報酬0扱いで平均。"""
            rewards = (
                batch["rewards"] if batch["rewards"].ndim == 1 else batch["rewards"][:, 0]
            )
            if "is_end" in batch:
                rewards = rewards * batch["is_end"]
            metrics = {
                "mean_reward": rewards.mean().item(),
                "frac_full_reward": (rewards >= 0.999).float().mean().item(),
                "frac_no_reward": (rewards <= 0.0).float().mean().item(),
                "num_samples": len(rewards),
            }
            return batch, metrics

        def global_post_init_hook(self, *args: Any, **kwargs: Any) -> None:
            pass

except ImportError:  # ローカル(CPU/DevContainer)では純関数部分のみ利用可
    pass
