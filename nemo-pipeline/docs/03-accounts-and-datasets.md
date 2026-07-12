# 03. 必要なアカウント一覧 & データセットのライセンス

## A. 必要なアカウント・APIキー（一覧）

| # | サービス | 用途（どのステージ） | 取得URL | 環境変数 / ログイン | 必須? | 費用 |
|---|---|---|---|---|---|---|
| 1 | **NGC (NVIDIA)** | NeMoコンテナ取得 (S3/S4/S5) | https://ngc.nvidia.com/setup | `docker login nvcr.io`（user=`$oauthtoken` / pass=APIキー） | 学習する場合 必須 | 無料 |
| 2 | **Anthropic API** | S2 蒸留の教師モデル | https://console.anthropic.com | `ANTHROPIC_API_KEY` | S2で必須 | 従量（$40〜120目安） |
| 3 | **Hugging Face** | S1 データ取得 / モデルDL | https://huggingface.co/settings/tokens | `HF_TOKEN`（`huggingface-cli login`） | データ取得時 | 無料 |
| 4 | **GPU環境**（下記いずれか） | S3/S4/S5 学習 | — | — | 学習する場合 必須 | 従量/自前 |
| 4a | ・ローカルGPU(64GB) | 〃（本命・保有時） | — | — | | 電気代のみ |
| 4b | ・AWS (EC2 g6e spot) | 〃 | IAM + EC2 | `aws configure` | | 従量（$60〜240目安） |
| 4c | ・NVIDIA Brev / DGX Cloud | 〃 | https://brev.nvidia.com | ブラウザ+CLI | | 従量 |
| 4d | ・Colab (Pro) | 軽量実験のみ | Googleアカウント | ブラウザ | | ~$10-50/月 |
| 5 | MLflow | 実験トラッキング（閉域・自前） | 不要（`make mlflow`で起動） | `MLFLOW_TRACKING_URI` | 任意 | 無料 |
| 6 | Langfuse | 推論トレース・評価/レール監視 | セルフホスト | `LANGFUSE_PUBLIC_KEY/SECRET_KEY` | 任意 | 無料 |

**最小構成で試すだけなら**: アカウント不要。`make setup && make curate distill-dry eval-dry`（S2本番のみ #2、学習は #1+#4、が追加で必要）。

**秘匿情報**: 平文 `.env` はコミット禁止（`.gitignore`済）。強いキーは `openssl rand -hex 32`、本番は SOPS+age 暗号化。

---

## B. データセットのライセンス（商用可否）

> 本パイプラインは商用利用を想定。**S1のライセンスゲートが非商用(NC)・改変禁止(ND)を自動で弾く**。
> ライブラリ既製データを使う場合は必ずHFカードで確認し、`--license` に正確な表記を渡すこと。

### ✅ 商用利用できる（推奨）
| データ/経路 | ライセンス | 用途 | 備考 |
|---|---|---|---|
| **官公庁の公開情報を自前収集** | 政府標準利用規約（**CC BY 4.0互換**） | ドメインSFT土台 | 国税庁/金融庁等のFAQ・解説。JaFInと同じ知識源を"自前で"集めれば商用可 |
| **EDINET / EDINET-Bench** | **PDL 1.0**（CC BY 4.0由来） | S6評価・財務テキスト | 金融庁EDINETの公開開示（有価証券報告書等）。会計不正検出等の実タスク |
| **合成データ（自社生成）** | own / synthetic | S2蒸留の主役 | 教師モデルで生成。機密を含めない限り最も自由 |
| 既製の商用可データ | Apache-2.0 / MIT / CC-BY / CC-BY-SA | 汎用維持・補強 | 例: llm-jp-instructions（要カード確認）, paraphrase-qa(CC-BY-SA) 等。CC-BY-SAは継承義務あり |
| **Nemotron-Post-Training-Dataset-v2** | 自由に学習・評価可（NVIDIA法務レビュー済） | SFT/RL補強（日本語含む） | Nemotron既定モデルと相性◎ |
| **Nemotron-Personas-Japan** | **CC BY 4.0** | 合成データの種・日本語多様性 | ペルソナ生成の種に |
| nri-fin-reasoning 等の金融指示データ | 要カード確認 | 金融SFT | 大規模だが**採用前にライセンス確認必須** |

### ❌ 商用利用できない（避ける／評価専用でも要注意）
| データ | ライセンス | 理由 |
|---|---|---|
| **JaFIn** | **CC BY-NC-SA 4.0** | **非商用**。出典は官公庁+Wikipediaだが配布ライセンスがNC → 商用パイプライン不可 |
| financial-lakehouse | ゲート付き非商用 | 商用利用・AI学習・再配布を明示的に禁止 |
| OCR_Task_JA | 非商用（JPX等） | 学術・非商用OCRベンチ用途に限定 |
| pfmt-bench-fin-ja 等の評価専用 | 各カード参照 | 評価専用。学習に使わない |

**原則**: 迷ったら (1)自社合成データ (2)官公庁公開情報の自前収集 (3)EDINET を軸にする。
外部の既製データはカードで Apache/MIT/CC-BY/CC-BY-SA/PDL を確認してから。**JaFInは使わない**。

---

## C. さらに
- 文書→データセット/メタデータの**作成テンプレート** → `docs/04-dataset-templates.md`（`templates/`）
- **独自データを将来学習に使う設計**（承認・PII・用途制御・来歴、**rag-onlyの扱い**含む） → `docs/05-proprietary-data-design.md`
