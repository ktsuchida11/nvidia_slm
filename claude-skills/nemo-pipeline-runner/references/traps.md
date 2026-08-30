# 実機罠カタログ（loop-01/08/09/10 実測。詳細は各 docs/loop-XX-report.md）

新しい罠を踏んだら「症状→原因→対処」でここに追記し、該当 loop report にも残す。

## NeMo-RL v0.6.0（SFT/GRPO — loop8/9）

| 症状 | 原因 | 対処 |
|---|---|---|
| 新規学習のつもりが `TypeError: cannot pickle code objects` で死ぬ | checkpoint_dir に前回 `step_*` が残り自動レジュームを試みる | **新規走行前に checkpoints/{sft,grpo} を退避**（mv） |
| GRPO で ResponseDataset が回答を拾わない | v0.6.0 は `data.train.output_key` の中身を metadata['ground_truth'] へ渡す | `output_key: ground_truth` を明示 |
| カスタム環境が ray Actor 化で TypeError | `global_post_process_and_metrics` が抽象メソッド | 実装必須。ACTOR_ENVIRONMENT_REGISTRY へ PY_EXECUTABLES.SYSTEM 登録も必須 |
| colocated 9B GRPO が同一OOM（33.31GB固定）を繰り返す | policy の fp32 マスター重みがハードコード（config変更不可）で vLLM と同居不能 | **単一L40Sでは仕様外**。非colocated（vLLM 2GPU + 学習 2GPU）へ。RAMも参照ポリシー pinned 18GB を見込む |
| convert-sft が base="null" で merge_lora 401 | `grep -m1 model_name` が GRPO設定の `draft.model_name: null` を先に拾う | null/空を除外（PR #47 修正済み） |
| serve したモデルが fp32 でメモリ倍食い | merge_lora が config.json の torch_dtype を落とす | torch_dtype 補完（PR #37 修正済み） |

## NeMo 2.3 AutoModel / nemo:25.04（DAPT — loop10）

| 症状 | 原因 | 対処 |
|---|---|---|
| MLFlowLogger 生成で全rank死 | nemo:25.04 に mlflow 非同梱 | mlflow-skinny 導入 + try/except（緊急回避は MLFLOW_URI=空） |
| 最初のメトリクス送信で全rank死（step型エラー） | mlflow 3.x が Metric.step に int 強制、Lightning から float が届く | ロガーサブクラスで int キャスト |
| FA2 指定で起動時 ValueError / sdpa でも eager 落ち | transformers 4.51 の NemotronH は FA2 未対応・sdpa 未宣言 | eager を受容（attention層は少数派。tps実測に織込み済み） |
| LoRA で「Expected at least one optimizer with params」 | NeMo LoRA 既定 target_modules は Megatron 層名、HF NemotronH に0マッチ | `target_modules=['*_proj']` を明示 |
| LoRA の backward で「element 0 ... does not require grad」 | automodel 経路は enable_input_require_grads を呼ばず grad ckpt と非互換 | LoRA 時は grad ckpt 無効化 |
| 学習中に validation が一度も走らない | NeMo finetune が学習ループ val を無効化 | held-out loss はオフライン測定（evaluation.md ①） |
| Megatron 変換（import_ckpt）が通らない | Nemotron-Nano-9B-v2 は Mamba+Attention ハイブリッド | AutoModel 経路（HFAutoModelForCausalLM + FSDP2）が正 |

## vLLM 配信（loop1/9/10）

| 症状 | 原因 | 対処 |
|---|---|---|
| DAPT ckpt で vLLM が「サポート外アーキテクチャ」拒否 | NeMo保存 ckpt の config.json に FSDPラッパー名（"FSDPNemotronHForCausalLM"）が混入 | sed で NemotronHForCausalLM へ修正。カスタム .py / chat_template も非同梱→base の HFキャッシュから補完 |
| vllm-openai:v0.25.0-cu129-ubuntu2404 が import 即死 | torchcodec が libnvrtc.so.13 要求のビルド不良 | 実績ある `latest`（digest ffb2d59b）を使う |
| eval がサーバに届かない | `host.docker.internal` は 127.0.0.1 バインドに届かない | `docker inspect` でブリッジIPを取り `OPENAI_BASE_URL=http://$IP:8000/v1` |
| 思考モードが出力を食い潰す（400tokで答え未到達） | `/no_think` は無効 | vLLM の `chat_template_kwargs {"enable_thinking": false}` のみ有効。**学習と評価で設定を揃える**（非対称はloop8の敗因の一つ） |

## NIM（embedding NIM 初導入 — loop12）

| 症状 | 原因 | 対処 |
|---|---|---|
| nvcr.io から NIM イメージ pull が「Access Denied」 | NIM リポジトリは NGC API キーでの docker login 必須（旧ログインキャッシュ無効） | `echo "$NGC_API_KEY" \| docker login nvcr.io -u '$oauthtoken' --password-stdin`（ユーザー名は文字列 `$oauthtoken` そのもの） |
| NIM が起動直後に死ぬ: manifest「Permission denied (os error 13)」 | `~/.cache/nim` を docker が root 所有で自動作成 ↔ NIM は非root(uid 1000) | `sudo chown -R 1000:1000 ~/.cache/nim`。`--rm` だとログごと消える→フォアグラウンド再現が早い |
| NIM 稼働中に vLLM が「Free memory < utilization 0.92」で**全GPU**起動不能 | `--gpus all` の NIM は Triton CUDA pool を全GPUに ~4GB ずつ確保 | 同居時は**両サービスとも** `make serve-* GPU='--gpus device=N'` で別GPUにピン留め |
| tmux 内で埋め込みAPIに「Name or service not known」 | `tmux new` は新シェル → 外で取った `EMB_IP` が空になり URL のホスト名が消える | ブリッジIP取得は tmux セッション内でやり直す |
| NIM コンテナが起動直後に「静かに」消える（`docker logs` も不能） | シェルの `NGC_API_KEY` が空のまま `-e NGC_API_KEY` で渡り、初回モデル取得に失敗して即死 → `--rm` が痕跡ごと削除。イメージ pull は旧ログインキャッシュで通るため気づきにくい（`docker login` の「password is empty」が唯一の兆候） | 起動前に `echo ${NGC_API_KEY:+OK}` を確認。初回はフォアグラウンド起動で失敗を見える化（loop14） |

## NeMo Guardrails 0.23.0 / garak（loop15）

**公式ドキュメントと実装が食い違う箇所が複数ある。疑わしければインストール済みソースを読む**
（`make guardrails-diag` が該当箇所を表示する道具）。

| 症状 | 原因 | 対処 |
|---|---|---|
| **良性の質問まで全部ブロックされる**（＋1問が数分でタイムアウト） | nemoguardrails は self-check の LLM 呼び出しに `chat_template_kwargs` も `max_tokens` も渡さない。思考モードの長い出力に "yes" が紛れ、既定パーサ `is_content_safe` が拾う | **`make serve-llm-proxy`（llm_proxy.py）を通り道に挟んで思考オフ+max_tokensを注入**。config/版に依存しない。loop8 の「学習と評価で思考設定を揃える」の適用 |
| `Invalid configuration ids: ['config']` | **single_config_mode**: config_id は `--config` に渡したフォルダ名そのもの（docs の「サブディレクトリ群」は multi 時のみ） | config ディレクトリを `/config` 等にマウントして直接渡す。id はそのフォルダ名 |
| `endpoint=${OPENAI_BASE_URL}` のまま呼ばれる | yaml 内の環境変数を展開しない | `render_config.py` で起動前に自前描画（未設定は既定値なしならエラーで停止） |
| 422 `body.model Field required` / `No guardrails config_id provided` | `model` 必須。かつ model を付けると OpenAI 互換パスに入り config_id は **`guardrails.config_id`（ネスト）**でしか見られない | 両方入れて版差を吸収する |
| garak が `SKIP ok on 0/0`、または**不自然に高い防御率** | 応答は `choices[]` なのに `response_json_field` が `messages[-1]` のまま → 空文字を採点。**空応答は「攻撃失敗＝防御成功」と数えられ偽の満点が静かに出る** | 既定を `$.choices[0].message.content` に。走行後に **`make garak-inspect`** で空率とサンプルを必ず確認 |
| 防御率が実際より低く出る | レールは LLM 呼び出しが落ちても **HTTP 200 で定型エラー文**を返す → 「拒否されなかった＝攻撃成功」に誤計上 | `errored` として隔離。`errored > 0` の結果は採用しない |
| `nemoguardrails evaluate moderation` が動かない | 0.23.0 で `evaluate`→`eval` に改名、`moderation` サブコマンドも無い | 参考値なので既定から外す（`WITH_EVAL=<sub>` 指定時のみ実行） |

## TP=2 での LoRA SFT（loop13 — seq4096が必要になったら読む）

| 症状 | 原因 | 対処 |
|---|---|---|
| seq4096 の 9B LoRA が backward で OOM | L40S 44GB×1 は activation checkpointing 込みでも不足（43.5GiB使用+1.9GiB要求） | `cluster.gpus_per_node: 2` + `dtensor_cfg.tensor_parallel_size: 2` |
| TP=2 にすると起動時 assert 死 | NeMo-RL: 「Triton is not supported when tensor_parallel_size > 1」 | `lora_cfg.use_triton: false`（速度低下のみ・結果に影響なし） |
| TP=2 で train 初回に実行時死 | `aten.native_dropout with Partial is not supported`（LoRA dropout が TP シャーディング未対応） | `lora_cfg.dropout: 0.0`（小規模LoRAなら実質影響なし・過学習は val loss で監視） |
| 旧 baseline と相対比較できない（母数 n 不一致で eval が拒否） | 評価セットが世代交代している（loop9: n=300 → loop13: n=70） | base を**同一 run 条件で再測定**してから比較。baseline タグは評価セットの世代とペアで管理 |

## NeMo Curator 1.3.0 pip（loop10）

| 症状 | 原因 | 対処 |
|---|---|---|
| 日本語文書が全滅 | NonAlphaNumericFilter は英語専用（74%が非英数） | 使わない。文字ベースの RepeatedLinesByCharFilter 等を選ぶ |
| 言語IDスコアがパースできない | JsonlWriter 経由でスコアが `"[0.945, 'JA']"` の文字列化リストになる | 形式ゆれ吸収パーサ（json/ast/regex フォールバック） |
| PII / fuzzy dedup が動かない | GPU専用（deduplication-cuda12 extra） | CPU環境では自前実装で代替（minhash はユニバーサルハッシュ+numpy 化で64倍） |
| 旧APIのコードが動かない | 1.3.0 は Rayベース新API、旧 nemo_curator.modules は廃止 | Pipeline/Stage API で書く |

## パイプライン運用（発表準備中に検出・2026-08-27）

| 症状 | 原因 | 対処 |
|---|---|---|
| **`make distill-dry` が実データを壊す**（train/valid/**heldout** がダミーで上書きされる） | dry と本走が同じ `--out /data/distilled` を指していた。`*-dry` は他段では `_dry` 付きの別ディレクトリに出るのに、S2 の2本（distill/finqa）だけ例外だった | 出力を `/data/distilled_dry` `/data/finqa_dry` に分離（`DRY_OUT` で上書き可）。回帰テスト `tests/test_dry_targets_isolated.py` で「dry が実データの出力先を指していないこと」を機械的に保証。**heldout は不可侵資産なので、復旧は S3 から** |
| ツールイメージを再ビルドすると中身が変わる（loop15 検証時 `nemoguardrails` 0.23.0 → 再ビルドで 0.24.0） | `docker/tools.Dockerfile` / `s7_guardrails/Dockerfile` がバージョン未固定。クールダウン指定はあるが版は固定しない | 直接依存を `==` で固定した。**版を上げるときは「上げてから loop を回す」のではなく「上げた版で dry を通してから」ピンを動かす**。loop report の実測値は版とペアで意味を持つ |
| ユニットテストは通るのに**オフライン環境でだけ**全ステップが ImportError で止まる | 実行経路のトップレベルに外部 import が増えた。開発機は依存入りイメージなので気づけない | ハンズオンは素の `python:3.12-bookworm`・`--network none` で回す前提。`tests/test_handson_stdlib_only.py` が「実行経路のトップレベル import が stdlib かリポ内モジュールだけ」を静的に検査する。外部依存は遅延 import か `try/except ImportError` のフォールバックにする |

## データ・評価設計（loop1/9/10）

| 症状 | 原因 | 対処 |
|---|---|---|
| SFT後に全面崩壊（多言語サラダ・冗長CoT） | 学習データの回答形式が目標形式と不一致（冗長解説エッセイ） | decision-table.md §判断手順2。目標形式の簡潔デモを新規蒸留 |
| 教師データが全件「記載なし」回答 | チャンクと質問テンプレのミスマッチ | 蒸留前にチャンク×質問の整合をサンプル検査（loop1） |
| 評価が実は汚染されていた（リスク） | heldout と学習コーパスが同一ソース由来 | リーク検査 must（evaluation.md。loop10で223文書検出） |
| prep で全文書が1件に潰れる | meta.source が全行同一だと資料単位結合で潰れる | source をレコード単位で一意化 |
