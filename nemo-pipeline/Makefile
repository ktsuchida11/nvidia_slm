# =============================================================================
# NeMoパイプライン 統一入口（K8s不要・Dockerのみで全ステージ動作）
#   ローカルLinux : setup / fetch / curate / distill(-dry) / eval(-dry) / guardrails*
#   借りGPUホスト : dapt / sft / grpo（このリポごと持ち込み同じmakeを叩く）
# =============================================================================
SHELL := /bin/bash
# DooD(Docker outside of Docker)環境ではデーモンがホスト側にあり、-v のソースは
# ホスト側パスが必要。hostpath.mk（gitignore済・機械固有）で HOST_PWD を上書きする。
-include hostpath.mk
HOST_PWD ?= $(PWD)
DATA := $(PWD)/data
CKPT := $(PWD)/checkpoints
RES  := $(PWD)/results
TAG  ?= model          # eval用: base / sft / grpo など

NEMO_IMG    ?= nvcr.io/nvidia/nemo:25.04          # 要NGCログイン。タグは導入時に最新確認（S3 DAPT用）
# S4/S5用（NeMo-RL統一）。latestタグは存在しない（実機確認済: v0.5.0/v0.6.0/v0.7.0）。
# v0.7.0はカタログ記載ありでもpull不可（2026-07実機確認）。pull可能な最新はv0.6.0
# sft_lora.yaml等のキー構造が準拠するバージョンと必ず揃える（RL_REFはclone時のブランチ/タグ）
RL_IMG      ?= nvcr.io/nvidia/nemo-rl:v0.6.0
RL_REF      ?= v0.6.0
CURATOR_IMG ?= nvcr.io/nvidia/nemo-curator:latest # 〃（S1はpython:3.12-slimでも可）
PY_IMG      ?= python:3.12-slim                    # 閉域では nemo-tools:latest（make build-tools）に差し替え

GPU := --gpus all
# DOCKER_NET: 起動コンテナを特定Dockerネットワークへ接続（任意・機械固有は hostpath.mk で指定）。
# 例: DevContainer(DooD)で LiteLLM ゲートウェイと同一ネットワークに乗せて http://litellm:4000 に到達する
RUN := docker run --rm -i $(if $(DOCKER_NET),--network $(DOCKER_NET)) --add-host=host.docker.internal:host-gateway -v $(HOST_PWD):/pipeline -v $(HOST_PWD)/data:/data -v $(HOST_PWD)/checkpoints:/ckpt -v $(HOST_PWD)/results:/results
MLFLOW_PORT ?= 5000
MLFLOW_IMG  ?= ghcr.io/mlflow/mlflow:latest       # 壊れている場合は nemo-tools:latest（mlflow同梱）
MLFLOW_URI ?= http://host.docker.internal:$(MLFLOW_PORT)
# データビューア用ポート（衝突時は hostpath.mk で上書き）
VIEWER_PORT ?= 8501

.PHONY: setup extract caption fetch curate distill relabel lint-labels relabel-lint augment eval-augment distill-dry finqa-filter finqa-build finqa-dry dapt sft grpo convert-sft convert-grpo serve-sft serve-grpo eval eval-dry eval-finqa eval-finqa-dry guardrails guardrails-test tracking tensorboard viewer clean

tracking:            ## 実験トラッキング(MLflow)を閉域で常駐起動 :5000
	mkdir -p $(PWD)/mlflow
	docker run --rm -d --name mlflow -p 127.0.0.1:$(MLFLOW_PORT):5000 -v $(HOST_PWD)/mlflow:/mlflow \
	  $(MLFLOW_IMG) mlflow server \
	  --backend-store-uri sqlite:////mlflow/mlflow.db \
	  --artifacts-destination /mlflow/artifacts --host 0.0.0.0 --port 5000
	@echo "MLflow UI: http://localhost:$(MLFLOW_PORT)（外部SaaSなし・完全閉域）"

tensorboard:         ## TensorBoard（サーバ不要の保険。学習ログを直接読む）
	docker run --rm -d --name tb -p 127.0.0.1:6006:6006 -v $(HOST_PWD)/checkpoints:/logs \
	  tensorflow/tensorflow:latest tensorboard --logdir /logs --host 0.0.0.0

viewer:              ## データビューア（curated/rejected/distilled の目視QC）:8501
	docker rm -f dataviewer 2>/dev/null || true
	docker run --rm -d --name dataviewer -p 127.0.0.1:$(VIEWER_PORT):8501 \
	  -v $(HOST_PWD):/pipeline:ro -v $(HOST_PWD)/data:/data:ro $(PY_IMG) bash -lc \
	  "(pip -q install streamlit 2>/dev/null || true) && \
	   streamlit run /pipeline/s1_curation/data_viewer.py \
	     --server.port 8501 --server.address 0.0.0.0 --server.headless true"
	@echo "データビューア: http://localhost:$(VIEWER_PORT)（外部送信なし・読み取り専用マウント）"

setup:               ## ディレクトリ契約の作成
	mkdir -p $(DATA)/raw $(DATA)/curated $(DATA)/distilled $(CKPT) $(RES)
	@echo "OK. NGC利用時は 'docker login nvcr.io' (user=\$$oauthtoken, pass=NGC APIキー)"

extract:             ## PPTX/PDF/DOCX → S1入力へ（軽量版・図/スキャンはNeMo Retriever推奨: docs/08）
	$(RUN) -v $(abspath $(or $(IN),./docs_in)):/docs_in:ro $(PY_IMG) bash -lc \
	  "(pip -q install pypdf python-pptx python-docx 2>/dev/null || true) && \
	   python /pipeline/s1_curation/convert_docs.py --in /docs_in --out /data/raw --license $(or $(LICENSE),own)"

caption:             ## 図/スキャンをフロンティアAPI(Claude vision)でキャプション/OCR化（要ANTHROPIC_API_KEY。dryは CAPTION_FLAGS=--dry-run）
	$(RUN) -e ANTHROPIC_API_KEY -v $(abspath $(or $(IN),./docs_in)):/docs_in:ro $(PY_IMG) bash -lc \
	  "(pip -q install anthropic pypdf python-pptx 2>/dev/null || true) && \
	   python /pipeline/s1_curation/caption_figures.py --in /docs_in --out /data/raw \
	   --license $(or $(LICENSE),own) --model $(or $(MODEL),claude-sonnet-4-6) $(CAPTION_FLAGS)"

fetch:               ## 金融データセット取得（商用可のみ。repo/licenseはHFカードで確認。docs/03参照。LIMIT=Nで件数制限、FIELDS=CSVでQA構造保持）
	$(RUN) $(PY_IMG) bash -lc "(pip -q install datasets huggingface_hub 2>/dev/null || true) && \
	  python /pipeline/s1_curation/fetch_dataset.py --repo $(REPO) --license $(LICENSE) --out /data/raw $(if $(LIMIT),--limit $(LIMIT)) $(if $(CONFIG),--config $(CONFIG)) $(if $(FIELDS),--keep-fields $(FIELDS))"

curate:              ## S1 収集・管理（CPUで可。Curator委譲は --engine curator）
	$(RUN) $(PY_IMG) python /pipeline/s1_curation/s1_curate.py --in /data/raw --out /data/curated

distill:             ## S2 蒸留（要 ANTHROPIC_API_KEY。LiteLLM経由は ANTHROPIC_BASE_URL/TEACHER_MODEL を指定）
	$(RUN) -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e TEACHER_MODEL $(PY_IMG) bash -lc "(pip -q install anthropic 2>/dev/null || true) && \
	  python /pipeline/s2_distillation/s2_distill.py --in /data/curated --out /data/distilled \
	  --n-analysis $(or $(N_ANALYSIS),300) --n-generation $(or $(N_GEN),100) \
	  --gen-chunks $(or $(GEN_CHUNKS),2) --chunk-chars $(or $(CHUNK_CHARS),700)"
relabel:             ## S2 analysisラベルのみ再生成（規約変更後。質問・分割・generation保持。要ANTHROPIC_API_KEY）
	$(RUN) -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e TEACHER_MODEL $(PY_IMG) bash -lc "(pip -q install anthropic 2>/dev/null || true) && \
	  python /pipeline/s2_distillation/s2_distill.py --in /data/curated --out /data/distilled --relabel"
lint-labels:         ## S2 ゴールド規約リント（API不要。違反一覧を data/distilled/lint_report.json へ）
	$(RUN) $(PY_IMG) python /pipeline/s2_distillation/s2_distill.py --in /data/curated --out /data/distilled --lint
relabel-lint:        ## S2 リント違反ゴールドのみ教師で再ラベル（要ANTHROPIC_API_KEY。先に make lint-labels）
	$(RUN) -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e TEACHER_MODEL $(PY_IMG) bash -lc "(pip -q install anthropic 2>/dev/null || true) && \
	  python /pipeline/s2_distillation/s2_distill.py --in /data/curated --out /data/distilled --relabel --only-lint"
augment:             ## S2 的絞りデータ増強（不足パターンをtrain/validへ追記。heldout不可侵。PATTERNS=CSVで限定。要ANTHROPIC_API_KEY）
	$(RUN) -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e TEACHER_MODEL $(PY_IMG) bash -lc "(pip -q install anthropic 2>/dev/null || true) && \
	  python /pipeline/s2_distillation/s2_distill.py --in /data/curated --out /data/distilled --augment $(if $(PATTERNS),--patterns $(PATTERNS))"
eval-augment:        ## S2 評価専用セット増強（heldout_ext.jsonlへ追記・学習不使用。N_EVAL=40。要ANTHROPIC_API_KEY）
	$(RUN) -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e TEACHER_MODEL $(PY_IMG) bash -lc "(pip -q install anthropic 2>/dev/null || true) && \
	  python /pipeline/s2_distillation/s2_distill.py --in /data/curated --out /data/distilled --eval-augment $(or $(N_EVAL),40)"
distill-dry:         ## S2 配管検証（API不要・数秒）
	$(RUN) $(PY_IMG) python /pipeline/s2_distillation/s2_distill.py \
	  --in /data/curated --out /data/distilled --dry-run \
	  --gen-chunks $(or $(GEN_CHUNKS),2) --chunk-chars $(or $(CHUNK_CHARS),700)

# --- ループ9: 金融QA(finqa)データ構築。先に make fetch REPO=... FIELDS=user,assistant ---
finqa-filter:        ## S2 finqa品質選別（機械翻訳QA→教師judge。要ANTHROPIC_API_KEY。FILTER_MODEL既定claude-haiku-4-5）
	$(RUN) -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e TEACHER_MODEL -e FILTER_MODEL $(PY_IMG) bash -lc "(pip -q install anthropic 2>/dev/null || true) && \
	  python /pipeline/s2_distillation/s2_finqa.py --src /data/raw --out /data/finqa --filter \
	  --target $(or $(TARGET),14000)"
finqa-build:         ## S2 finqa SFT/GRPO/heldout構築（教師verifyで数値答え正規化+リーク検査。要ANTHROPIC_API_KEY）
	$(RUN) -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e TEACHER_MODEL $(PY_IMG) bash -lc "(pip -q install anthropic 2>/dev/null || true) && \
	  python /pipeline/s2_distillation/s2_finqa.py --src /data/raw --out /data/finqa --build \
	  --n-sft $(or $(N_SFT),10000) --n-grpo $(or $(N_GRPO),2500) --n-heldout $(or $(N_HELDOUT),300)"
finqa-dry:           ## S2 finqa配管検証（API不要・数秒）
	$(RUN) $(PY_IMG) python /pipeline/s2_distillation/s2_finqa.py \
	  --src /data/raw --out /data/finqa --filter --build --dry-run

# --- 実験トラッキング（クローズド網: 自前MLflow。外部送信なし） ----------------
build-tools:         ## 閉域準備: 依存焼き込みツールイメージをbuild（オンライン環境で一度実行）
	docker build -t nemo-tools:latest --build-arg BASE_IMAGE=$(or $(TOOLS_BASE),python:3.12-slim) -f docker/tools.Dockerfile .
	@echo "以後: make <target> PY_IMG=nemo-tools:latest で実行時pip不要"

serve-nemotron:      ## 推論配信（Nemotron-Nano-9B-v2-Japanese / vLLM）:8002。要 HF_TOKEN（gated）
	docker run --rm -d --name llm-gen $(GPU) --ipc=host \
	  -p 127.0.0.1:8002:8000 -e HUGGING_FACE_HUB_TOKEN=$(HF_TOKEN) \
	  -v $${HF_HOME:-$$HOME/.cache/huggingface}:/root/.cache/huggingface \
	  vllm/vllm-openai:latest \
	  --model nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese --served-model-name nemotron-gen \
	  --host 0.0.0.0 --port 8000 --trust-remote-code --mamba_ssm_cache_dtype float32 \
	  --max-num-seqs 64 --max-model-len 131072
	@echo "OPENAI_BASE_URL=http://localhost:8002/v1 で make eval（初回はDL/ロードで数分）"

convert-sft:         ## SFTチェックポイント(DCP+LoRA)→HF形式へ変換・マージ（STEP=path指定可、既定は最新step）
	$(RUN) -e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN=$(HF_TOKEN) \
	  -v $${HF_HOME:-$$HOME/.cache/huggingface}:/root/.cache/huggingface \
	  $(RL_IMG) bash /pipeline/s4_sft/convert_sft_to_hf.sh $(STEP)

convert-grpo:        ## GRPOチェックポイント(LoRA)→HF形式へ変換・マージ（ベースはckptのconfig.yaml記載のmodel_name=/ckpt/sft/hf）
	$(RUN) -e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN=$(HF_TOKEN) -e CKPT_ROOT=/ckpt/grpo \
	  -v $${HF_HOME:-$$HOME/.cache/huggingface}:/root/.cache/huggingface \
	  $(RL_IMG) bash /pipeline/s4_sft/convert_sft_to_hf.sh $(STEP)

serve-sft:           ## SFT済みモデル配信（vLLM）:8002。要 make convert-sft 済み
	docker run --rm -d --name llm-gen $(GPU) --ipc=host \
	  -p 127.0.0.1:8002:8000 -e HUGGING_FACE_HUB_TOKEN=$(HF_TOKEN) \
	  -v $${HF_HOME:-$$HOME/.cache/huggingface}:/root/.cache/huggingface \
	  -v $(HOST_PWD)/checkpoints:/ckpt \
	  vllm/vllm-openai:latest \
	  --model /ckpt/sft/hf --served-model-name nemotron-gen \
	  --host 0.0.0.0 --port 8000 --trust-remote-code --mamba_ssm_cache_dtype float32 \
	  --max-num-seqs 64 --max-model-len 131072
	@echo "起動確認: docker logs -f llm-gen → 'Application startup complete' 後に make eval TAG=sft"

serve-grpo:          ## GRPO済みモデル配信（vLLM）:8002。要 make convert-grpo 済み
	docker run --rm -d --name llm-gen $(GPU) --ipc=host \
	  -p 127.0.0.1:8002:8000 -e HUGGING_FACE_HUB_TOKEN=$(HF_TOKEN) \
	  -v $${HF_HOME:-$$HOME/.cache/huggingface}:/root/.cache/huggingface \
	  -v $(HOST_PWD)/checkpoints:/ckpt \
	  vllm/vllm-openai:latest \
	  --model /ckpt/grpo/hf --served-model-name nemotron-gen \
	  --host 0.0.0.0 --port 8000 --trust-remote-code --mamba_ssm_cache_dtype float32 \
	  --max-num-seqs 64 --max-model-len 131072
	@echo "起動確認: docker logs -f llm-gen → 'Application startup complete' 後に make eval TAG=grpo"

serve:               ## （任意・代替）GGUF対応モデル(Qwen等)用のllama.cpp配信。既定Nemotronは serve-nemotron を使う
	docker run --rm -d --name llm-$(or $(ALIAS),qwen3-gen) $(GPU) \
	  -p 127.0.0.1:$(or $(PORT),8002):8000 -v $(HOST_PWD)/models:/models:ro \
	  ghcr.io/ggml-org/llama.cpp:server-cuda \
	  -m /models/$(or $(GGUF),qwen3-gen.gguf) --host 0.0.0.0 --port 8000 \
	  -ngl 99 --parallel 4 -c 16384 --jinja --alias $(or $(ALIAS),qwen3-gen)
	@echo "OPENAI_BASE_URL=http://localhost:$(or $(PORT),8002)/v1 で make eval へ"

mlflow:              ## MLflowサーバ起動（:5000, データはresults/mlruns に永続化）
	docker run --rm -d --name mlflow -p 127.0.0.1:$(MLFLOW_PORT):5000 \
	  -v $(HOST_PWD)/results/mlruns:/mlruns $(MLFLOW_IMG) \
	  mlflow server --host 0.0.0.0 --port 5000 \
	    --backend-store-uri sqlite:////mlruns/mlflow.db --artifacts-destination /mlruns/artifacts
	@echo "UI: http://localhost:$(MLFLOW_PORT)  (学習側は MLFLOW_TRACKING_URI=$(MLFLOW_URI))"

# --- 学習（ローカル64GB or 借りGPU。K8s不要、Dockerのみ） ---------------------
MLFLOW_URI ?= http://host.docker.internal:5000
TRACK := -e MLFLOW_TRACKING_URI=$(MLFLOW_URI) --add-host=host.docker.internal:host-gateway

dapt:                ## S3 継続事前学習（任意）
	$(RUN) $(GPU) $(TRACK) --shm-size=16g $(NEMO_IMG) bash /pipeline/s3_pretraining/run_dapt.sh
prep-rl:             ## S4/S5用データ変換（distilled → NeMo-RL ResponseDataset形式。CPUで可）
	$(RUN) $(PY_IMG) python /pipeline/s5_rl/prep_rl_data.py --in /data/distilled --out /data/distilled/rl
sft:                 ## S4 SFT/LoRA（NeMo-RL: examples/run_sft.py。設定差し替えは SFT_CFG=/pipeline/s4_sft/sft_finqa.yaml）
	$(RUN) $(GPU) $(TRACK) --shm-size=16g -e RL_REF=$(RL_REF) -e SFT_CFG \
	  -v $${HF_HOME:-$$HOME/.cache/huggingface}:/root/.cache/huggingface \
	  -e RAY_memory_monitor_refresh_ms=0 \
	  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
	  $(RL_IMG) bash /pipeline/s4_sft/run_sft.sh
GRPO_CFG ?= grpo_qwen.yaml
# GRPO_DOCKER_FLAGS: ホストRAM保護用の追加dockerフラグ（例: 64GBホストで
#   GRPO_DOCKER_FLAGS='--memory=56g --memory-swap=88g' — コンテナ内OOMに閉じ込め
#   SSM/ホストの窒息を防ぐ。ループ8実機の教訓）
# shm: Rayオブジェクトストアは/dev/shm内に確保され proportion(0.15)×空きRAM まで要求する。
#   shm不足だとRayが/tmpへフォールバックし劣化するため、shmは 0.15×ホストRAM を収める値にする
#   (g6e.12xlarge 384GB → 0.15×~370GB≈55GB < 64g)。tmpfsは遅延割当のため未使用分はRAMを食わない
grpo:                ## S5 GRPO（NeMo-RL + カスタム報酬環境。analysis用は GRPO_CFG=grpo_analysis.yaml。OOM時 PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:64）
	$(RUN) $(GPU) $(TRACK) --shm-size=64g $(GRPO_DOCKER_FLAGS) -e PIPELINE_DIR=/pipeline \
	  -e RAY_memory_monitor_refresh_ms=0 \
	  -e RAY_DEFAULT_OBJECT_STORE_MEMORY_PROPORTION=0.15 \
	  $(RL_IMG) bash -lc \
	  "if [ -d /opt/nemo-rl/examples ]; then cd /opt/nemo-rl; \
	   elif [ -d /pipeline/vendor/RL ]; then cd /pipeline/vendor/RL; \
	   else git clone --depth 1 --recursive --branch $(RL_REF) https://github.com/NVIDIA-NeMo/RL /rl && cd /rl; fi && \
	   PYTHONPATH=/pipeline/s5_rl:$$PYTHONPATH \
	   uv run /pipeline/s5_rl/run_grpo_finance.py --config /pipeline/s5_rl/$(GRPO_CFG)"
vendor-rl:           ## 閉域準備: NeMo-RLをリポ内に取り込み（--recursiveでsubmodule込み・オンライン環境で一度）
	git clone --depth 1 --recursive https://github.com/NVIDIA-NeMo/RL vendor/RL

# --- 評価（OPENAI_BASE_URL=推論エンドポイント。TAG=base|sft|grpo） ------------
eval:                ## S6 本評価（exit 0=合格 / 2=閾値未達）。相対合否は BASELINE=<tag> を指定（例: TAG=grpo8 BASELINE=base8）。Nemotronは EVAL_CHAT_KWARGS='{"enable_thinking": false}' で思考抑止
	$(RUN) -e OPENAI_BASE_URL -e OPENAI_API_KEY -e EVAL_SYS_PREFIX -e EVAL_CHAT_KWARGS $(PY_IMG) bash -lc "(pip -q install pyyaml 2>/dev/null || true) && \
	  python /pipeline/s6_evaluation/s6_eval.py --config /pipeline/s6_evaluation/eval.yaml --tag $(TAG) $(if $(BASELINE),--baseline $(BASELINE))"
eval-dry:            ## S6 配管検証（エンドポイント不要・全指標満点になるはず）
	$(RUN) $(PY_IMG) bash -lc "(pip -q install pyyaml 2>/dev/null || true) && \
	  python /pipeline/s6_evaluation/s6_eval.py --config /pipeline/s6_evaluation/eval.yaml --mode dry --tag dry"
eval-finqa:          ## S6 finqa評価（ループ9。TAG=base9|sft9|grpo9、改善確認は BASELINE=<tag>）
	$(RUN) -e OPENAI_BASE_URL -e OPENAI_API_KEY -e EVAL_SYS_PREFIX -e EVAL_CHAT_KWARGS $(PY_IMG) bash -lc "(pip -q install pyyaml 2>/dev/null || true) && \
	  python /pipeline/s6_evaluation/s6_eval.py --config /pipeline/s6_evaluation/eval_finqa.yaml --tag $(TAG) $(if $(BASELINE),--baseline $(BASELINE))"
eval-finqa-dry:      ## S6 finqa配管検証（エンドポイント不要）
	$(RUN) $(PY_IMG) bash -lc "(pip -q install pyyaml 2>/dev/null || true) && \
	  python /pipeline/s6_evaluation/s6_eval.py --config /pipeline/s6_evaluation/eval_finqa.yaml --mode dry --tag finqa_dry"

# --- S7 ガードレール ----------------------------------------------------------
GUARD_PORT ?= 8100
guardrails:          ## サーバ起動（既定:8100。衝突時は GUARD_PORT で変更）
	docker run --rm -d --name guardrails -p 127.0.0.1:$(GUARD_PORT):8100 \
	  -v $(HOST_PWD)/s7_guardrails/config:/config $(PY_IMG) bash -lc \
	  "(pip -q install nemoguardrails[all] 2>/dev/null || true) && nemoguardrails server --config /config --port 8100"
guardrails-test:     ## 検証（公式evaluate + garak攻撃。GUARD_PORTをgarak設定に反映）
	$(RUN) --add-host=host.docker.internal:host-gateway $(PY_IMG) bash -lc \
	  "(pip -q install nemoguardrails[all] garak 2>/dev/null || true) && \
	   sed 's/:8100/:$(GUARD_PORT)/' /pipeline/s7_guardrails/garak_rest.json > /tmp/garak_rest.json && \
	   nemoguardrails evaluate moderation --config /pipeline/s7_guardrails/config && \
	   python -m garak --model_type rest --generator_option_file /tmp/garak_rest.json \
	     --probes promptinject --report_prefix /results/garak"

clean:
	docker rm -f guardrails mlflow tb dataviewer 2>/dev/null || true
