"""S5 GRPO 起動ラッパ — カスタム環境を登録してから NeMo-RL の examples/run_grpo.py を実行する。

使い方（NeMo-RLリポのルートで。make grpo が自動実行）:
  PIPELINE_DIR=/pipeline PYTHONPATH=/pipeline/s5_rl \
    uv run /pipeline/s5_rl/run_grpo_finance.py --config /pipeline/s5_rl/grpo_qwen.yaml

仕組み:
  - register_env で "finance_grounding" を ENV_REGISTRY へ追加（NeMo-RL公式API）
  - ワーカー側は actor_class_fqn "finance_env.FinanceGroundingEnvironment" を import するため
    PYTHONPATH に /pipeline/s5_rl が必要（create_env が os.environ をワーカーへ伝播する）
  - その後 examples/run_grpo.py を __main__ として実行（引数はそのまま透過）
"""
import os
import pathlib
import runpy
import sys

os.environ.setdefault("PIPELINE_DIR", "/pipeline")
env_dir = str(pathlib.Path(__file__).resolve().parent)
if env_dir not in sys.path:
    sys.path.insert(0, env_dir)
os.environ["PYTHONPATH"] = env_dir + os.pathsep + os.environ.get("PYTHONPATH", "")

from nemo_rl.distributed.ray_actor_environment_registry import ACTOR_ENVIRONMENT_REGISTRY  # noqa: E402
from nemo_rl.distributed.virtual_cluster import PY_EXECUTABLES  # noqa: E402
from nemo_rl.environments.utils import ENV_REGISTRY, register_env  # noqa: E402

if "finance_grounding" not in ENV_REGISTRY:
    register_env("finance_grounding", "finance_env.FinanceGroundingEnvironment")

# v0.6.0: カスタムActorはPython実行環境の登録も必須（無いと create_env が
# get_actor_python_env で ValueError — 実機ループ8で発現）。
# 報酬は正規表現+標準ライブラリのみで特殊依存なし → SYSTEM（ドライバと同じ環境）
ACTOR_ENVIRONMENT_REGISTRY.setdefault(
    "finance_env.FinanceGroundingEnvironment", PY_EXECUTABLES.SYSTEM)

# NeMo-RLリポのルート（examples/ がある場所）から実行される前提
run_grpo = pathlib.Path("examples/run_grpo.py")
assert run_grpo.exists(), (
    f"examples/run_grpo.py が見つかりません（cwd={os.getcwd()}）。"
    "NeMo-RLリポのルートで実行してください（make grpo は vendor/RL または clone 先で実行する）")
runpy.run_path(str(run_grpo), run_name="__main__")
