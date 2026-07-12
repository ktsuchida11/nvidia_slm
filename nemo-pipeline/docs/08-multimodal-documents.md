# 08. PowerPoint / PDF（図表入り文書）を学習データに使う設計

> 現行S1はテキスト(jsonl)前提。**PPTX/PDF・図・表・チャートを含む文書**を学習に使うには、
> S1の手前に**抽出ステージ「S1e 文書抽出」**を足す。本書はその設計と運用ルール。

## 0. 全体像（S1の手前に1段）
```
[PPTX/PDF/DOCX/スキャン] → ★S1e 文書抽出（要素分類・OCR・図のキャプション化）
      → 要素別テキスト+来歴メタ(jsonl) → S1 収集・管理（既存: 品質/PII/ライセンス/重複排除）→ S2 →…
```

## 1. 抽出ツール：NeMo Retriever Library（旧 NVIDIA Ingest / nv-ingest）
NVIDIAスタックで完結する本命。オープンソースのGPU加速インジェスト基盤。
- **対応形式**: pdf / pptx / docx / html / 画像(png,jpeg,tiff,bmp) / md / txt / json 等
- **要素分類**: ページをテキスト/表/チャート/インフォグラフィックに分類し抽出、OCR（Nemotron OCR v2・多言語）
- **PDF抽出モード**: `pdfium`(既定・最速) / `pdfium_hybrid`(ネイティブ+スキャンページ自動OCR) / `ocr`(全ページOCR) / `nemotron_parse`
- **Office**: python-docx/python-pptx のネイティブ抽出（速い）or `render_as_pdf`（LibreOffice経由・レイアウト忠実）
- **図のキャプション生成**: `.caption()` で **VLM（既定 nvidia/nemotron-nano-12b-v2-vl）** が画像/インフォグラフィックの説明文を生成
- **規模**: 100 PDF未満は**ライブラリモード（ローカルGPU/HFモデル）**で可 → **K8s不要の方針を維持**。
  大規模はDocker Compose、本番スケールはK8s+Helm（=将来のmicroservices側の話）
- 参照: https://github.com/NVIDIA/NeMo-Retriever / https://docs.nvidia.com/nemo/retriever/

**軽量代替（閉域・小規模・CPUのみ）**: `s1_curation/convert_docs.py`（同梱）。pypdf/python-pptxで
テキスト・表・ノートだけ抽出する簡易版。図の理解はできない（マーカーを残す）。

**フロンティアAPI代替（GPU不要・実装済み）**: `s1_curation/caption_figures.py`（同梱）。
PDF/PPTXの**埋め込み画像を抽出**し、**Claude(vision)** で図のキャプション生成・スキャンOCRを実行。
- 図→「数値・ラベルは読み取れるものだけ、推測禁止」の幻覚対策プロンプト／スキャン→文字起こし（[判読不能]明示）
- **同一画像はAPI 1回**（ハッシュキャッシュ）・レコードは出現箇所ごと。`--max-images`でコスト上限
- provenanceに `api-vlm:<model>` を記録＝教師出力として忠実性ゲート/人手確認の対象（§5）
- 選び方: **閉域厳格→NeMo Retriever（ローカルGPU）** ／ **手早く高品質→API**（機微画像を外に出せる場合のみ）。
  ※機微度が confidential 以上の画像はAPIに送らない（docs/05の決定表に従う）

## 2. 要素タイプ別の学習利用ポリシー（ここが肝）
| 要素 | 抽出方法 | 学習(train)での扱い | 注意 |
|---|---|---|---|
| 本文テキスト | ネイティブ抽出 | ◎ 通常どおり | 読み順（2段組・テキストボックス順）を確認 |
| **表** | 表構造抽出→**Markdown表** | ◎ 表は**1チャンクに収める**（分割禁止） | 数値の根拠として優秀。number_grounded報酬と好相性 |
| **チャート/グラフ** | 図中テキストOCR＋(任意)VLM説明 | △ **数値の言い換えは検証後のみ**。原則は「チャートの説明文」として | VLMの読み取り誤り（数値幻覚）に注意→忠実性ゲート必須 |
| **図解・構成図** | **VLMキャプション** | △ キャプション文をtrainに。原図は**rag-only**（索引に画像+説明） | provenance.method=vlm-caption を必ず記録 |
| スキャンPDF | `pdfium_hybrid`/OCR | ○ OCR品質スコアでゲート | 低品質OCRは学習を汚す→閾値未満は落とす |
| 写真・スクリーンショット | VLM/OCR | **原則never**（PII・認証情報・画面情報の温床） | 検出したら人手レビュー行き |
| 発表者ノート(pptx) | ネイティブ抽出 | ○ 口語の良い教材。ただし機微発言に注意 | sensitivity判定は§docs/05の決定表 |

**原則**:
1. **学習に入るのは常に「テキスト化された表現」**（本文/Markdown表/検証済みキャプション）。画像そのものは学習しない（本パイプラインはLLMでありVLM学習はスコープ外）。
2. **図表由来のテキストには必ず来歴**（modality / page / 抽出方法 / キャプションモデル）を付ける → 誤りの遡及と除外が可能に。
3. **VLMキャプションは"生成物"**＝幻覚し得る。S2と同じ**忠実性ゲート**（原文書の数値・固有名と突合）を通す。
4. 図が主役の資料（構成図集など）は **rag-only が第一候補**（画像+キャプションで検索・回答参照、学習はしない）。

## 3. チャンク化ルール（スライド/ページ）
- **PPTX: 1スライド=1チャンク**を基本（タイトル+本文+ノートを結合）。表は独立チャンク。
- PDF: 見出し単位300〜800字（既存ルール）。**表・図キャプションは分割しない**。
- 同一資料の版違い（v1/v2デッキ）は既存のfuzzy重複排除が吸収。ページ順・章構成をmetaに残す。

## 4. メタデータ拡張（record.schema.json に追加済み）
- `meta.source_format`: pdf / pptx / docx / html / txt …
- `meta.locator`: ページ/スライド番号（例 "p.12" / "slide 8"）
- `meta.modality`: text / table / chart / figure-caption / ocr / speaker-notes
- `meta.provenance.extraction`: pdfium / pdfium_hybrid / ocr / python-pptx / vlm-caption(モデル名)

## 5. 品質ゲート（実装済み分と設計）
**実装済み**（E2E検証: TS-09）:
- S1が未処理マーカー（`provenance.extraction=skipped`＝図/スキャン）を `needs_extraction` として**隔離**
  → rejected.jsonl から NeMo Retriever（OCR/VLM）処理に回して再投入する運用。
- S3コーパス生成（prep_corpus.py）は**資料(source)単位で結合**してから長さ判定
  （スライド1枚は短くてもデッキ全体で採取）。マーカー・非対象modalityは除外。

**設計（実機で追加）**:
- OCR信頼度 < 閾値 → reject（reject_reason: ocr_quality）
- VLMキャプション → 原文書内の数値・固有名詞と突合し、根拠なき数値を含むものは reject（faithfulness）
- 画像からのPII/認証情報検出（スクリーンショット等）→ never / 人手レビュー

## 6. 実行方法
```bash
# A) 本命: NeMo Retriever Library（ローカルGPU・<100 PDF）
uv venv retriever --python 3.12 && source retriever/bin/activate
uv pip install "nemo-retriever[local]"
# Ingestor().files([...]).extract(extract_text/tables/charts/infographics).caption() → JSON → /data/raw へ変換

# B) 軽量: 同梱スクリプト（CPU・テキスト/表/ノートのみ。図/スキャンはマーカー化）
make extract IN=./docs_in           # convert_docs.py が /data/raw/*.jsonl を生成

# B') フロンティアAPI: 図/スキャンを Claude(vision) で処理（GPU不要・要APIキー）
export ANTHROPIC_API_KEY=sk-ant-...
make caption IN=./docs_in           # figures_captioned.jsonl を生成（B と併用: extract→caption→curate）
#   dry検証: make caption IN=./docs_in CAPTION_FLAGS=--dry-run
```
GPUはVLMキャプション/OCRで使用（64GBで十分）。学習ジョブとは時間帯を分ける。

## 7. 勉強会での論点
- 「図をどう学習させるか」→ **図は学習しない。図の"検証済み説明文"を学習し、原図はrag-onlyで参照** が現実解。
- VLMキャプションの幻覚を、既存の source_exists / number_grounded と同じ思想でゲートする一貫性。
