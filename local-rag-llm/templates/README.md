# 環境雛形（CPU/API面 + GPU面 + DevContainer）

設計書 `local-rag-llm-implementation-design.md` 12章に対応する起動スケルトン。

## ファイル
```
.devcontainer/devcontainer.json      # 開発(Claude Code)。compose統合型
.devcontainer/docker-compose.dev.yml #   devサービス定義(compose.ymlと合成)
.devcontainer/Dockerfile             #   uv + Python3.12 + sops/age(バージョン固定)
compose.yml                          # CPU/API面（全OS共通）: db/litellm/presidio + app(profile)
compose.gpu.yml                      # GPU面（Linux/WSL2・本番推論専用）
litellm_config.yaml                  # モデル一覧 + フォールバック
.env.example                         # → cp .env.example .env（実体はSOPS推奨）
```
**アプリリポ直下**に配置（`compose.yml` の `build: .` は既存アプリのDockerfileを指す）。

## 役割分担（設計どおり）
- **開発** = DevContainer（CPU/API面）。GPU不要。
- **学習** = Colab/EC2。**ローカルでは学習しない**ため学習サービスは無い。
- **本番推論** = 自前GPUホスト（Linux or WindowsのWSL2）。`compose.gpu.yml`。

## 初回セットアップ
```bash
cp .env.example .env                       # 値を設定（本番はSOPS復号で生成）
docker network create langfuse_default     # Langfuse未起動でも参照エラーを防ぐ
```

## 起動パターン
```bash
# 1) 開発（Mac等・GPUなし）: インフラだけ起動 → VS Codeで「Reopen in Container」
docker compose up -d                       # db / litellm / presidio
#    ローカルモデル宛ての呼び出しは fallbacks で自動的に Claude へ（開発が止まらない）

# 2) デモ/本番CPU面（app込み）
docker compose --profile app up -d

# 3) GPUホスト（Linux / WindowsのWSL2）: 本番推論も同梱
#    事前に ./models/ に qwen3-analysis.gguf / qwen3-gen.gguf を配置
docker compose -f compose.yml -f compose.gpu.yml --profile app up -d
```

## 接続の原則（重要）
- **コンテナ間はサービス名**で直結: `db:5432` / `litellm:4000` / `llm-analysis:8000` / `llm-gen:8000`。
- `127.0.0.1:xxxx` のポート公開は**ホストからの疎通確認用**。
  - ⚠ `host.docker.internal` + `127.0.0.1`バインドの組み合わせは **Linuxでは通らない**
    （Docker Desktopのみの挙動）。コンテナ間通信に使わないこと。
- 別GPU機へ委譲する場合のみ `.env` の `QWEN_*_BASE` を `http://<GPU機IP>:8001/v1` 等に上書きし、
  GPU機側は `0.0.0.0` バインド + FWで送信元IP制限 + **可能ならmTLS/トークン認証**（素の公開は禁止）。

## フォールバック（2層あることに注意）
1. **ゲートウェイ層**（litellm_config.yaml の `fallbacks`）: 接続断・5xx → Claude に自動切替。
2. **アプリ層**（routing.py の品質ゲート）: `source_exists` 等の不合格 → Claude で再生成。
   ゲートウェイ層は品質を見ない。両方必要。

## OS別メモ
- **Mac(Apple Silicon)**: GPUコンテナ不可（CUDA無）。推論はMLXネイティブ or 別GPU機へ委譲。
- **Linux**: NVIDIA Container Toolkit 導入後 `--gpus all` を確認。
- **Windows(将来)**: 開発も推論も **WSL2** 経由（Docker Desktop 4.30+）。
  - vLLMはネイティブWindows非対応 → WSL2上で。単機なら llama.cpp が堅い。Qwen3.5はOllama不可。
  - RTX5090(Blackwell, sm_120)はWSL2のドライバパスに固有の注意（設計書12.3）。



## 📊 監視環境の構築（Langfuse / MLflow）
役割分担: **Langfuse=推論トレース・評価・レール発火の記録（アプリ側）** / **MLflow=学習曲線（nemo-pipeline側, `make mlflow`）**。

### Langfuse v3（セルフホスト・閉域可）を立てる
本composeは **公式スタックに相乗り**する設計（`langfuse_default` 外部ネットワークに接続）。
```bash
# 1) 公式リポの compose を取得して起動（閉域なら事前に clone / docker save で持ち込み）
git clone https://github.com/langfuse/langfuse.git && cd langfuse
docker compose up -d          # プロジェクト名 langfuse → ネットワーク名は自動で langfuse_default になる
# 2) http://localhost:3000 で組織/プロジェクト作成 → Public/Secret キー発行
# 3) 本アプリ側 .env に LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY を設定（LANGFUSE_HOSTは既定でOK）
# 4) 本アプリを起動（compose.yml は langfuse_default に自動接続）
```
- Langfuseを使わない場合: `.env` でキー未設定のまま可（トレース無効）。`langfuse_default` が無いと
  compose起動に失敗するため `docker network create langfuse_default` で空作成しておく。
- ポートやDB(clickhouse等)は公式composeが管理。バージョン更新も公式に追従。

## 🔒 セキュリティ（本番前チェックリスト）
- [ ] `.env` は `.gitignore` 済み。強いキーは `openssl rand -hex 32`。本番は SOPS+age 暗号化。
- [ ] `LITELLM_MASTER_KEY` / `POSTGRES_PASSWORD` / `STREAMLIT_PASSWORD` を強値に（未設定だと compose 起動が失敗する設計）。
- [ ] app(8501) は `127.0.0.1` バインド。外部提供は **TLS + 認証つきリバースプロキシ**（例: nginx/Caddy + Basic/OIDC）経由のみ。
- [ ] 全サービスに `no-new-privileges:true` 付与済み。DBは localhost バインド。
- [ ] 別GPU機へ委譲（`QWEN_*_BASE` を外部IPに）する場合は **専用セグメント + 送信元IP制限 + できれば mTLS**。推論エンドポイントを素で公開しない（未認証だと誰でも生成させられる＝コスト/情報漏洩）。
- [ ] `models/` `data/` はコミットしない（.gitignore済）。実データ投入時はPIIレール(Presidio)を有効化。
- [ ] ガードレール（Phase 3）は「監視のみ」ではなく**ブロック**で本番投入。garakでインジェクション成功率を定期計測。

> 補足: LiteLLM 経由の生成は `LITELLM_MASTER_KEY` で認証。キー未設定運用（旧デフォルト `sk-local-master`）は禁止。

## 朝ピークのサイジング
llama.cpp は `--parallel N` で同時Nリクエスト処理（既定は逐次）。本雛形は解析4/生成4スロット。
レポート配信直後の同時実行数に合わせて `--parallel` と `-c`（合計コンテキスト）を調整する。

## 接続図
```
app / dev ──(litellm:4000/v1)── LiteLLM ─┬─ anthropic/claude-sonnet-4-6（難ケース/フォールバック先）
                                          ├─ anthropic/claude-haiku-4-5 （解析フォールバック先）
                                          ├─ openai/qwen3-analysis → llm-analysis:8000（ローカル4B）
                                          └─ openai/qwen3-gen      → llm-gen:8000     （ローカル9B）
```
