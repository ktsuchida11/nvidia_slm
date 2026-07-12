# 公開前 最終検証記録

| 観点 | 項目 | 結果 |
|---|---|---|
| 動作 | 全Python構文コンパイル | ✅ |
| 動作 | 全YAML/JSON構文 | ✅ |
| 動作 | Makefile全ターゲットparse | ✅ |
| 動作 | E2Eスモーク(S1→S2→S6 dry, pass:true) | ✅ |
| 動作 | S1重複排除/PIIマスク/ライセンスゲート実挙動 | ✅ |
| 環境 | 入口ドキュメント6種の存在 | ✅ |
| 環境 | API/GPU不要の検証導線(dry-run) | ✅ |
| 環境 | docs相互リンク切れ | ✅ なし |
| 環境 | K8s不要の明記 | ✅ |
| セキュリティ | 実APIキー/トークン/秘密鍵の混入 | ✅ なし |
| セキュリティ | 弱いデフォルト秘密フォールバック | ✅ なし(:? で必須化) |
| セキュリティ | 非localhost公開ポート | ✅ なし(全て127.0.0.1) |
| セキュリティ | .env実体/鍵ファイルの混入 | ✅ なし |
| セキュリティ | .gitignore網羅(.env/models/gguf/results/age.key) | ✅ |
| セキュリティ | 顧客固有情報(社名/ペルソナ/銘柄) | ✅ 一般化済 |
| セキュリティ | 特権実行/docker.sock/hostネットワーク | ✅ なし |
| セキュリティ | no-new-privileges 付与 | ✅ 全サービス |
| 環境 | 構成図(architecture-overview.svg) XML構文 | ✅ |
| 環境 | 構成図 枠外/テキストはみ出し/ボックス重なり検査 | ✅ なし |
| セキュリティ | データセット商用可否（JaFIn等NC除外・ライセンスゲート単体テスト） | ✅ 全ケース正 |
| 環境 | 必要アカウント一覧・データセットライセンス表(docs/03) | ✅ |
| 環境 | 構成図にNeMoライブラリ表記・凡例 | ✅ |
| 環境 | メイン図の「使う」を配信/評価に簡素化 | ✅ |
| 環境 | 使う（推論構成）を別図に分離(serving-routing-overview.svg・座標検査OK) | ✅ |
| 環境 | データセット/メタデータ作成テンプレート(04+templates) | ✅ JSON/YAML有効 |
| 環境 | 独自データ取込の設計(05・設計のみ／rag-only含む) | ✅ |
| 環境 | データ分類演習(21・記入例9種、決定表と自動検算で整合) | ✅ |
| 環境 | 既定モデルNemotron対応(06/vLLM配信/学習config) | ✅ |
| 環境 | 量子化記述をパイプライン対応方式(vLLM fp8)に統一・GGUFは代替明記 | ✅ |
| セキュリティ | モデルライセンス(NVIDIA Nemotron Open・商用可)明記 | ✅ |
| 環境 | OpenShell出口ガバナンスの補完設計(07・任意/アルファ明記) | ✅ |
| 環境 | 図表入り文書(PPTX/PDF)の学習データ化設計(08+convert_docs+schema拡張) | ✅ |
| 環境 | S3 DAPT具体化(コーパス生成実装/MLflowロガー/64GB目安) | ✅ |
| 環境 | Langfuse構築手順(公式compose相乗り)明文化・MLflowはmake mlflowで構築済 | ✅ |
| 動作 | **図表入りPDF/PPTX→抽出→S1→DAPTコーパスのE2E実走**（実PDF/PPTX生成・マーカー隔離・資料単位結合, TS-09） | ✅ |
| 動作 | 図/スキャンの**フロンティアAPIルート**(caption_figures)E2E実走（実画像抽出・キャッシュ課金1回・OCR/キャプションのS1→コーパス統合） | ✅ |
| 環境 | 主目的（NeMoシリーズでパイプライン構築・稼働）をREADMEに明示 | ✅ |
| 環境 | Claude Codeスキル(nemo-pipeline-runner: 評価ループ自動運転・承認ゲート付) | ✅ |
| 権利 | ライセンス | MIT + 免責明記 |


## 実現可能性チェック（一次情報で確認）
| 懸念 | 結果 | 根拠 |
|---|---|---|
| NeMo-RLでNemotron-H(Mamba)のGRPO可否 | ✅ 対応済 | NVIDIAがNemotron NanoをNeMo-RLで学習・Nano系GRPO/SFT LoRAレシピ実在 |
| 単一64GB GPUでのGRPO | ✅ 可 | DTensor backend + LoRA GRPO対応。OOMは max_split_size_mb で緩和 |
| 配信=vLLM | ✅ 整合 | NeMo-RLのrollout backendがvLLM。mamba_ssm_cache_dtype float32必須 |
| reasoningモデルの構造化出力/採点 | ✅ 対処済 | reasoning-parserで最終回答分離 or system prompt無効化。s6_evalに除去安全網 |
| モデル商用利用 | ✅ 可 | NVIDIA Nemotron Open Model License（商用可） |
| 学習データの商用利用 | ✅ 可 | Nemotron-Post-Training-v2/Personas-Japan(CC BY 4.0)。JaFIn(NC)は排除 |
| 残る要確認 | vLLM/NeMo-RLコンテナの版整合（実機で最新タグ確認）、grpo実キー名はレシピ準拠 |

判定: **公開可**（学習・検証目的の一般化サンプルとして）。
実運用へ移す際は各docsのセキュリティ節・本番前チェックリストに従うこと。
