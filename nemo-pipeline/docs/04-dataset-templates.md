# 04. 文書→データセット/メタデータ作成の推奨テンプレート

文書から学習データを作るときの、レコード形式・データセットメタデータ・作成レシピの標準とテンプレート。
テンプレート実体は `templates/`。

## A. レコード形式（1学習例）
広く使われる2形式。どちらも `templates/record.schema.json` の `meta` を付けて来歴を残す。

| 形式 | いつ使う | 形 |
|---|---|---|
| **Alpaca** | 単発の指示応答・分類・抽出 | `{instruction, input, output}` |
| **OpenAI/ShareGPT (messages)** | 多ターン・system付き・RAG | `{messages:[{role,content}...]}` |

- 本パイプラインの S2 出力もこの形（analysis=構造化output / generation=messages相当）。
- **必ず `meta` を付与**：source・license・provenance・pii_status・sensitivity・allowed_use・split。
  → `templates/record.schema.json` で検証できる（商用可ライセンス・neverは学習除外、を型で表現）。

## B. データセット単位のメタデータ（2層で持つ）
| 層 | 何 | テンプレート | 目的 |
|---|---|---|---|
| 人間可読 | **データセットカード**（Datasheets / Data Cards準拠） | `templates/dataset_card.md` | 出所・権利・PII・用途を人が確認 |
| 機械可読 | **Croissant**（MLCommons標準・HF/Kaggle/OpenML採用） | `templates/croissant.metadata.json` | 発見可能性・ローダ連携・**監査/ガバナンス** |

- Croissantは JSON-LD。**Croissant-RAI** 拡張で来歴(**PROV-O**)と許諾(**DUO**)を機械可読に持てる → 監査が速い。
- 検証・生成は MLCommons croissant ライブラリ/エディタ（github.com/mlcommons/croissant）。

> **PPTX/PDF・図表入り文書**が入力の場合は、先に `docs/08-multimodal-documents.md`（S1e抽出・要素別ポリシー）。

## C. 文書→データセットの作成レシピ（S1→S2に対応）
```
文書 → ①取込 → ②クリーニング（正規化/boilerplate除去）→ ③チャンク化 →
④生成（QA/指示を教師モデルで合成 or 抽出）→ ⑤検証（スキーマ/忠実性）→
⑥来歴スタンプ（license/provenance/pii/allowed_use）→ ⑦分割（train/valid/heldout）
```
- ①〜③・⑥ = **S1（s1_curate.py）**、④⑤⑦ = **S2（s2_distill.py）** が対応。
- **チャンク化の目安**：意味のまとまりで 300〜800字、重複50〜100字。表・箇条書きは崩さない。
- **QA合成**：チャンクを根拠に「質問＋出典付き回答」を生成し、`source_exists` で忠実性を検証（作話を除外）。
- NeMo Curator の SDG（合成データ生成）パイプラインもこの④に使える。
- **学習に使わない社内資料は④⑦を省き、⑥まで（クリーニング＋来歴＋acl付与）でRAG索引へ**（rag-only。設計は docs/05 §7）。

## D. 品質・重複・PII（S1が実施済み）
- 重複排除：exact + fuzzy(MinHash)。品質：日本語比率・記号率・反復。PII：マスク。ライセンス：商用可のみ通過。
- これらは既に s1_curate.py に実装。独自データでも同じゲートを通す。

## 参照
- Croissant: https://mlcommons.org/croissant / https://github.com/mlcommons/croissant
- Datasheets for Datasets (Gebru et al.) / Data Cards (Pushkarna et al.)
- HF Dataset Cards: https://huggingface.co/docs/hub/datasets-cards
