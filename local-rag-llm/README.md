# 対象アプリ ローカルLLM プロジェクト一式

> ℹ 本書は特定顧客の固有情報を除いた**一般化版**です。対象は「金融マーケットレポート向けRAGアシスタント」という汎用像で、セクター体系・コスト数値・執筆者スタイルは例示（自社の実値に置換して使用）。

既存RAGチャットアプリに、ローカル学習モデル＋LiteLLMルーティング＋ガードレールを最小差分で組み込むための設計・構成一式。

> ⚠ **これは設計であって実装ではありません。** `src/` は未着手です。
> **載せるモデルは `nemo-pipeline/` で作ります**（そちらは15周まわした実装があります）。
> 2026-08 に判明した重要な点: **Phase 1（クエリ解析）は nemo-pipeline の `analysis` タスクと
> 同じもの**で、出力スキーマ・評価指標・学習データが既に揃っています。
> しかも `analysis_match` は **base（未学習）で 0.836** あり、合格ライン 0.85 との差は
> 測定誤差の範囲です。**学習せずに配信設定だけで終わる可能性が高い**ので、
> 着手前に `nemo-pipeline/docs/38-query-analysis-runbook.md` を読んでください。

## 中身
```
local-rag-llm/
├── README.md                                # 本ファイル（索引）
├── serving-routing-overview.svg             # 推論構成図（直列フロー・ルーティング2箇所）
├── local-llm-study-deck-outline.md          # 勉強会構成案（作る・使う・守る）
├── local-rag-llm-implementation-design.md   # 実装設計書（Phase 1〜4 / 評価 / 統合 / 環境 / セキュリティ）
└── templates/                               # 検証済み環境スケルトン
    ├── README.md                            #   起動パターン・接続原則・OS別メモ
    ├── compose.yml                          #   CPU/API面（全OS共通、appはprofile）
    ├── compose.gpu.yml                      #   GPU面（Linux/WSL2・本番推論専用）
    ├── litellm_config.yaml                  #   モデル一覧 + fallbacks
    ├── .env.example
    └── .devcontainer/                       #   compose統合型（Claude Code開発）
        ├── devcontainer.json
        ├── docker-compose.dev.yml
        └── Dockerfile
```

## 読む順番
1. `local-rag-llm-implementation-design.md` … 設計の本体。末尾チェックリストが作業の地図。
2. `templates/README.md` … 環境の建て方（接続の原則・フォールバック2層は必読）。
3. `local-llm-study-deck-outline.md` … 意思決定の経緯。

## ローカルで Claude Code を使って進める手順
1. `templates/` の中身を既存アプリリポ直下にコピー。
2. `cp .env.example .env` + `docker network create langfuse_default`。
3. `docker compose up -d`（インフラのみ）→ VS Code「Reopen in Container」。
   GPU未接続でも fallbacks により全経路Claudeで動く＝開発が止まらない。
4. Claude Code に設計書を読ませ、チェックリストを上から1つずつ実装依頼。
5. 学習は Colab/EC2、本番推論は自前GPUホスト（Linux or WindowsのWSL2）。

## 設計の前提（要点）
- 開発=DevContainer（CPU/API面）/ **学習=`nemo-pipeline/`（AWS g6e spot = L40S 48GB で15周実績）** / 本番推論=自前GPUホスト
- 評価ファースト: 指標・閾値・held-out評価セットを先に用意し **base を測ってから学習の要否を決める**
  （loop9 では base が強いタスクに SFT を当てて 0.836 → 0.115 に壊した）
- コンテナ間はサービス名直結（host.docker.internal頼みはLinuxで壊れる）
- **配信は vLLM 固定**。Nemotron-Nano-9B-v2 は Mamba+Attention ハイブリッドで GGUF 非対応、
  Qwen3.5 も Ollama 非対応。**Ollama / llama.cpp は使えない**（好みではなくモデル側の制約）
- 分岐は全部ルールベース（LLM に「難しいか」を判定させない）。ルーティング点は①クエリ解析と③回答生成の2箇所
- 経済性の数値は見積もり。実測で再確認
