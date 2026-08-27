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

.PHONY: setup extract caption fetch fetch-edinet-bench curate curate-curator distill dapt-prep probe-gen probe-eval relabel lint-labels relabel-lint augment eval-augment distill-dry finqa-filter finqa-build finqa-dry dapt sft grpo convert-sft convert-grpo serve-sft serve-grpo eval eval-dry eval-finqa eval-finqa-dry guardrails guardrails-test guardrails-test-raw guardrails-check guardrails-dry guardrails-dry-rails serve-embed retriever-index retriever-index-dry retriever-recall retriever-recall-dry rag-eval rag-eval-dry serve-rerank rag-rerank-dry tracking tensorboard viewer clean

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

fetch-edinet-bench:  ## loop10 P1 Tier1: EDINET-Bench全configの有報本文を平文化して/data/rawへ（doc_id一意化・横断dedup。LIMIT=Nで動作確認）
	$(RUN) $(PY_IMG) bash -lc "(pip -q install datasets 2>/dev/null || true) && \
	  python /pipeline/s1_curation/fetch_edinet_bench.py --out /data/raw $(if $(LIMIT),--limit $(LIMIT))"

fetch:               ## 金融データセット取得（商用可のみ。repo/licenseはHFカードで確認。docs/03参照。LIMIT=Nで件数制限、FIELDS=CSVでQA構造保持）
	$(RUN) $(PY_IMG) bash -lc "(pip -q install datasets huggingface_hub 2>/dev/null || true) && \
	  python /pipeline/s1_curation/fetch_dataset.py --repo $(REPO) --license $(LICENSE) --out /data/raw $(if $(LIMIT),--limit $(LIMIT)) $(if $(CONFIG),--config $(CONFIG)) $(if $(FIELDS),--keep-fields $(FIELDS))"

curate:              ## S1 収集・管理（CPUで可。Curator委譲は --engine curator）
	$(RUN) $(PY_IMG) python /pipeline/s1_curation/s1_curate.py --in /data/raw --out /data/curated

# fasttext が C++ コンパイルを要するため gcc 入り bookworm を既定にする（slim 不可）
CURATOR_PY_IMG ?= python:3.12-bookworm
curate-curator:      ## loop10 P2: Curator前段(言語ID+品質)→本段curate を連続実行。初回は pip install で数分
	$(RUN) $(CURATOR_PY_IMG) bash -lc "(pip -q install 'nemo-curator[text-cpu]' numpy 2>/dev/null || true) && \
	  python /pipeline/s1_curation/curator_stage.py --in /data/raw --out /data/raw_curator && \
	  python /pipeline/s1_curation/s1_curate.py --in /data/raw_curator --out /data/curated"

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
	  --n-sft $(or $(N_SFT),10000) --n-grpo $(or $(N_GRPO),2500) --n-heldout $(or $(N_HELDOUT),300) --n-fmt $(or $(N_FMT),1500)"
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

probe-gen:           ## loop10 P5: probe種文書からknowledge-probe QAを教師生成（要ANTHROPIC_API_KEY。Macから。中断再開可）
	$(RUN) -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e TEACHER_MODEL $(PY_IMG) bash -lc "(pip -q install anthropic 2>/dev/null || true) && \
	  python /pipeline/s6_evaluation/probe_qa.py --generate $(if $(N_PER_DOC),--n-per-doc $(N_PER_DOC))"
probe-eval:          ## loop10 P5: closed-book probe評価（配信中モデルへ。TAG=dapt10等・採点ローカル）
	$(RUN) -e OPENAI_BASE_URL -e OPENAI_API_KEY -e EVAL_CHAT_KWARGS $(PY_IMG) bash -lc "(pip -q install openai 2>/dev/null || true) && \
	  python /pipeline/s6_evaluation/probe_qa.py --eval --tag $(TAG)"

dapt:                ## S3 継続事前学習（loop10で実体化。DAPT_CFG=/pipeline/s3_pretraining/dapt.yaml、GPU数はDAPT_GPUS）
	$(RUN) $(GPU) $(TRACK) --shm-size=16g -e DAPT_CFG -e DAPT_GPUS -e DAPT_REPREP -e DAPT_PIP_UPGRADE -e HF_TOKEN \
	  -v $${HF_HOME:-$$HOME/.cache/huggingface}:/root/.cache/huggingface \
	  $(NEMO_IMG) bash /pipeline/s3_pretraining/run_dapt.sh
dapt-prep:           ## S3 コーパス準備のみ（CPU可・リーク検査+3分割。ローカルで事前確認用）
	$(RUN) $(PY_IMG) python /pipeline/s3_pretraining/prep_corpus.py --in /data/curated/curated.jsonl --out /data/pretrain
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

# --- S7 ガードレール（loop15 実機検証。docs/loop-15-plan.md） -----------------
GUARD_PORT   ?= 8100
GUARD_MODEL  ?= nemotron-gen           # vLLM の --served-model-name と config.yml に一致させる
GARAK_PROBES ?= promptinject,dan,encoding   # OWASP LLM01 系の標準ベンチ（英語）
GARAK_GENS   ?= 1                      # 生成回数。既定の複数生成はGPU時間に直結するため1に絞る
# nemoguardrails の応答は版により messages[] / OpenAI互換 choices[] のどちらもありうる。
# スモークで空文字が返るなら GARAK_RESP_FIELD='$$.choices[0].message.content' に切り替える
GARAK_RESP_FIELD ?= $$.messages[-1].content
N_PROBE      ?= 14                     # 良性セットに足す probe 質問数（固定6問 + これ）
guardrails:          ## サーバ起動（既定:8100。衝突時は GUARD_PORT で変更）
	docker run --rm -d --name guardrails -p 127.0.0.1:$(GUARD_PORT):8100 \
	  --add-host=host.docker.internal:host-gateway \
	  -e OPENAI_BASE_URL -e OPENAI_API_KEY=$${OPENAI_API_KEY:-dummy} \
	  -v $(HOST_PWD)/s7_guardrails:/configs $(PY_IMG) bash -lc \
	  "(pip -q install nemoguardrails[all] 2>/dev/null || true) && nemoguardrails server --config /configs --port 8100"
	@echo "起動確認: docker logs -f guardrails → 'Uvicorn running'（config_id=config で :$(GUARD_PORT) へPOST）"

guardrails-test:     ## B2: garak標準ベンチ（レールあり :8100）+ 公式evaluate
	$(RUN) $(PY_IMG) bash -lc \
	  "(pip -q install nemoguardrails[all] garak 2>/dev/null || true) && \
	   python /pipeline/s7_guardrails/make_garak_cfg.py --template /pipeline/s7_guardrails/garak_rest.json \
	     --out /tmp/garak_rest.json --port $(GUARD_PORT) --model $(GUARD_MODEL) \
	     --resp-field '$(GARAK_RESP_FIELD)' && \
	   nemoguardrails evaluate moderation --config /pipeline/s7_guardrails/config; \
	   python -m garak --model_type rest --generator_option_file /tmp/garak_rest.json \
	     --probes $(GARAK_PROBES) --generations $(GARAK_GENS) --report_prefix /results/garak_rails"
guardrails-test-raw: ## B1: garak標準ベンチのベースライン（レールなし・vLLM直 :8002）
	$(RUN) -e OPENAI_API_KEY $(PY_IMG) bash -lc \
	  "(pip -q install garak 2>/dev/null || true) && \
	   python /pipeline/s7_guardrails/make_garak_cfg.py --template /pipeline/s7_guardrails/garak_rest_raw.json \
	     --out /tmp/garak_rest_raw.json --model $(GUARD_MODEL) && \
	   python -m garak --model_type rest --generator_option_file /tmp/garak_rest_raw.json \
	     --probes $(GARAK_PROBES) --generations $(GARAK_GENS) --report_prefix /results/garak_raw"

guardrails-check:    ## B3/B4: 日本語カスタム攻撃・良性チェック（SET=attack|benign, API=guardrails|openai）
	$(RUN) -e OPENAI_API_KEY -e GUARD_MODEL=$(GUARD_MODEL) \
	  -e GUARD_BASE_URL=$${GUARD_BASE_URL:-http://host.docker.internal:$(GUARD_PORT)} -e OPENAI_BASE_URL \
	  $(PY_IMG) python /pipeline/s7_guardrails/check_rails.py \
	    --api $(or $(API),guardrails) --set $(or $(SET),attack) --n-probe $(N_PROBE) \
	    --tag $(or $(TAG),rails)

guardrails-dry:      ## loop15 配管検証: スタブLLM×チェッカー（GPU/API不要・数秒）
	$(RUN) $(PY_IMG) bash -lc \
	  "python /pipeline/s7_guardrails/stub_llm.py --port 8002 & sleep 2 && \
	   python /pipeline/s7_guardrails/check_rails.py --api openai --set attack \
	     --base-url http://127.0.0.1:8002/v1 --tag dry_attack --out /results && \
	   python /pipeline/s7_guardrails/check_rails.py --api openai --set benign \
	     --base-url http://127.0.0.1:8002/v1 --tag dry_benign --n-probe $(N_PROBE) --out /results"
	@echo "期待: attack は ok_rate 低（スタブは拒否しない）/ benign は ok_rate 1.0（=誤爆ゼロ）"
guardrails-dry-rails: ## loop15 配管検証(レール込み): スタブLLM + 実nemoguardrails（数分・GPU不要）
	$(RUN) $(PY_IMG) bash -lc \
	  "(pip -q install nemoguardrails[all] 2>/dev/null || true) && \
	   python /pipeline/s7_guardrails/stub_llm.py --port 8002 & sleep 2 && \
	   OPENAI_BASE_URL=http://127.0.0.1:8002/v1 OPENAI_API_KEY=dummy \
	     nemoguardrails server --config /pipeline/s7_guardrails --port 8100 & \
	   for i in \$$(seq 1 60); do curl -sf http://127.0.0.1:8100/v1/rails/configs >/dev/null && break; sleep 2; done; \
	   python /pipeline/s7_guardrails/check_rails.py --api guardrails --set attack \
	     --base-url http://127.0.0.1:8100 --tag dry_rails --out /results"

# --- S8 Retriever（loop12: RAG vs DAPT費用対効果比較。docs/loop-12-plan.md） --
EMBED_PORT ?= 8001
NIM_EMBED_IMG ?= nvcr.io/nim/nvidia/llama-3.2-nv-embedqa-1b-v2:latest
serve-embed:         ## loop12: NeMo Retriever embedding NIM 起動 :8001（ノード・要 docker login nvcr.io + NGC_API_KEY）
	docker run --rm -d --name embed-nim $(GPU) --shm-size=16g \
	  -p 127.0.0.1:$(EMBED_PORT):8000 -e NGC_API_KEY \
	  -v $${HOME}/.cache/nim:/opt/nim/.cache $(NIM_EMBED_IMG)
	@echo "起動確認: docker logs -f embed-nim → 'Uvicorn running' 後に make retriever-index"

retriever-index:     ## loop12: corpus→チャンク→埋め込み→インデックス（中断再開可・再開時は再課金ゼロ。EMBED_BACKEND=nim|hf|dummy、LIMIT=Nで配管検証）
	$(RUN) -e EMBED_BACKEND -e EMBED_BASE_URL -e EMBED_MODEL -e NGC_API_KEY $(PY_IMG) bash -lc \
	  "(pip -q install numpy openai 2>/dev/null || true) && \
	   python /pipeline/s8_retrieval/build_index.py --in $(or $(CORPUS),/data/pretrain/corpus_train.jsonl) \
	     --out $(or $(INDEX),/data/retriever) $(if $(LIMIT),--limit $(LIMIT))"
retriever-index-dry: ## loop12 配管検証: dummy埋め込み×probe50文書（GPU/API不要・数秒）
	$(RUN) -e EMBED_BACKEND=dummy $(PY_IMG) bash -lc "(pip -q install numpy 2>/dev/null || true) && \
	  python /pipeline/s8_retrieval/build_index.py --in /data/pretrain/corpus_probe.jsonl --out /data/retriever_dry"
retriever-recall:    ## loop12 評価①: 検索品質 recall@k/MRR（gold doc_id照合・採点ローカル。TAG=recall_nim等）
	$(RUN) -e EMBED_BACKEND -e EMBED_BASE_URL -e EMBED_MODEL -e NGC_API_KEY $(PY_IMG) bash -lc \
	  "(pip -q install numpy openai 2>/dev/null || true) && \
	   python /pipeline/s8_retrieval/recall_eval.py --index $(or $(INDEX),/data/retriever) --tag $(TAG)"
retriever-recall-dry: ## loop12 配管検証: dryインデックスでrecall配管を通す（GPU/API不要）
	$(RUN) -e EMBED_BACKEND=dummy $(PY_IMG) bash -lc "(pip -q install numpy 2>/dev/null || true) && \
	  python /pipeline/s8_retrieval/recall_eval.py --index /data/retriever_dry --tag recall_dry --only-indexed"
rag-eval:            ## loop12 評価②: base+RAG probe評価（要 配信中モデル+インデックス。TAG=rag199・RAG_K=5。loop14: RERANK=1 RERANK_K0=50 で rerank 経由）
	$(RUN) -e OPENAI_BASE_URL -e OPENAI_API_KEY -e EVAL_CHAT_KWARGS -e EMBED_BACKEND -e EMBED_BASE_URL -e EMBED_MODEL -e RERANK_BACKEND -e RERANK_BASE_URL -e RERANK_MODEL -e NGC_API_KEY $(PY_IMG) bash -lc \
	  "(pip -q install numpy openai 2>/dev/null || true) && \
	   python /pipeline/s6_evaluation/probe_qa.py --eval --rag --index $(or $(INDEX),/data/retriever) \
	     --rag-k $(or $(RAG_K),5) $(if $(RERANK),--rerank --rerank-k0 $(or $(RERANK_K0),50)) --tag $(TAG)"
rag-eval-dry:        ## loop12 配管検証: RAG評価の配管（配信・API不要。検索は実行・生成はgoldモック=正答率1.0が正常）
	$(RUN) -e EMBED_BACKEND=dummy $(PY_IMG) bash -lc "(pip -q install numpy 2>/dev/null || true) && \
	  python /pipeline/s6_evaluation/probe_qa.py --eval --rag --dry-run --index /data/retriever_dry --tag rag_dry"

# --- loop14: rerank NIM（検索律速の解消。docs/loop-14-plan.md） ---------------
RERANK_PORT ?= 8003
NIM_RERANK_IMG ?= nvcr.io/nim/nvidia/llama-3.2-nv-rerankqa-1b-v2:latest
serve-rerank:        ## loop14: rerank NIM 起動 :8003（ノード・要 docker login nvcr.io + NGC_API_KEY。embed と同居: GPU='--gpus device=0'）
	docker run --rm -d --name rerank-nim $(GPU) --shm-size=16g \
	  -p 127.0.0.1:$(RERANK_PORT):8000 -e NGC_API_KEY \
	  -v $${HOME}/.cache/nim:/opt/nim/.cache $(NIM_RERANK_IMG)
	@echo "起動確認: docker logs -f rerank-nim → 'Uvicorn running' 後に make rag-eval RERANK=1"
rag-rerank-dry:      ## loop14 配管検証: rerank込みRAG評価の配管（$0。dummy埋め込み+dummy rerank・生成goldモック=正答率1.0が正常）
	$(RUN) -e EMBED_BACKEND=dummy -e RERANK_BACKEND=dummy $(PY_IMG) bash -lc \
	  "(pip -q install numpy 2>/dev/null || true) && \
	   python /pipeline/s6_evaluation/probe_qa.py --eval --rag --rerank --rerank-k0 20 --dry-run \
	     --index /data/retriever_dry --tag rag_rerank_dry"

# --- loop13: grounded QA SFT（読解ミス23ppの回収。docs/loop-13-plan.md） ------
grounded-seed:       ## loop13: probe-50以外から種文書を決定的サンプル（$0・CPU。N_DOCS=500）
	$(RUN) $(PY_IMG) python /pipeline/s8_retrieval/build_grounded_sft.py --seed \
	  --n-docs $(or $(N_DOCS),500)
grounded-gen:        ## loop13 ゲートC: 教師QA生成（要ANTHROPIC_API_KEY・承認後。単価実測は GEN_DOCS=13、全量は指定なし。追記式で再課金ゼロ再開）
	$(RUN) -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e TEACHER_MODEL $(PY_IMG) bash -lc \
	  "(pip -q install anthropic 2>/dev/null || true) && \
	   $(if $(GEN_DOCS),head -n $(GEN_DOCS) /data/pretrain/grounded_seed.jsonl > /tmp/seed.jsonl &&,) \
	   python /pipeline/s6_evaluation/probe_qa.py --generate \
	     --probe $(if $(GEN_DOCS),/tmp/seed.jsonl,/data/pretrain/grounded_seed.jsonl) \
	     --qa /data/pretrain/grounded_qa.jsonl $(if $(N_PER_DOC),--n-per-doc $(N_PER_DOC))"
grounded-build:      ## loop13: リーク検査→実検索top5→SFTデータ化（ノード: 要NIM稼働+全量インデックス → /data/pretrain/grounded/）
	$(RUN) -e EMBED_BACKEND -e EMBED_BASE_URL -e EMBED_MODEL -e NGC_API_KEY $(PY_IMG) bash -lc \
	  "(pip -q install numpy openai 2>/dev/null || true) && \
	   python /pipeline/s8_retrieval/build_grounded_sft.py --build --index $(or $(INDEX),/data/retriever) \
	     $(if $(RAG_K),--k $(RAG_K))"
grounded-dry:        ## loop13 配管検証: seed→合成QA→dummy索引→build を自己完結で通す（$0・数秒）
	$(RUN) $(PY_IMG) bash -lc "(pip -q install numpy 2>/dev/null || true) && \
	  python /pipeline/s8_retrieval/build_grounded_sft.py --dry --out /data/pretrain/grounded_dry"
grounded-sft:        ## loop13 ゲートA/B: LoRA SFT（承認後。ckptは/ckpt/sft_grounded に分離=loop8罠対策）
	$(MAKE) sft SFT_CFG=/pipeline/s4_sft/sft_grounded.yaml
grounded-convert:    ## loop13: sft_grounded ckpt→HF変換（/ckpt/sft_grounded/hf。convert-sftは/ckpt/sft/hf=loop9資産を上書きするため専用化）
	$(RUN) -e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN=$(HF_TOKEN) -e CKPT_ROOT=/ckpt/sft_grounded \
	  -v $${HF_HOME:-$$HOME/.cache/huggingface}:/root/.cache/huggingface \
	  $(RL_IMG) bash /pipeline/s4_sft/convert_sft_to_hf.sh $(STEP)
grounded-serve:      ## loop13: sft13配信（vLLM）:8002。要 make grounded-convert 済み
	docker run --rm -d --name llm-gen $(GPU) --ipc=host \
	  -p 127.0.0.1:8002:8000 -e HUGGING_FACE_HUB_TOKEN=$(HF_TOKEN) \
	  -v $${HF_HOME:-$$HOME/.cache/huggingface}:/root/.cache/huggingface \
	  -v $(HOST_PWD)/checkpoints:/ckpt \
	  vllm/vllm-openai:latest \
	  --model /ckpt/sft_grounded/hf --served-model-name nemotron-gen \
	  --host 0.0.0.0 --port 8000 --trust-remote-code --mamba_ssm_cache_dtype float32 \
	  --max-num-seqs 64 --max-model-len 131072
	@echo "起動確認: docker logs -f llm-gen → 'Application startup complete' 後に make rag-eval TAG=sft13"

clean:
	docker rm -f guardrails mlflow tb dataviewer embed-nim rerank-nim 2>/dev/null || true
