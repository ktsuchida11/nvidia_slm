# 学習ループ 1周目レポート（2026-07-17）

## 実施内容

g6e.xlarge (L40S 48GB) スポットで base測定 → SFT/LoRA → eval を完走。

- **base測定**: Nemotron-Nano-9B-v2-Japanese / vLLM / `EVAL_CHAT_KWARGS='{"enable_thinking": false}'`
- **SFT**: NeMo-RL v0.6.0 / LoRA(dim16, α32) / seq2048 / 2epoch=6step / loss 8.92→7.48, val 7.10
- **変換**: PEFTアダプタ→safetensorsシャード直接マージ（`make convert-sft`）→ `make serve-sft`

## 結果（heldout n=10）

| 指標 | base | SFT | 閾値 |
|---|---|---|---|
| schema_valid | 1.0 | 1.0 | 1.0 ✅ |
| sectors_match | 0.556 | 0.556 | - |
| query_type_match | 0.778 | **0.889** | - |
| date_range_match | 0.111 | **0.222** | - |
| analysis_match | 0.0 | 0.0 | 0.85 ❌ |
| source_exists | 1.0 | **0.0** | 1.0 ❌ |
| citation_format | 1.0 | **0.0** | 0.95 ❌ |

## 主要な発見（このループの成果）

**教師データの構造的欠陥を検出した。**

1. **generation 学習データは 17/17 件すべてが「レポートに記載がありません」回答**。
   チャンク（EDINET由来の企業沿革・社史）と質問（市況系テンプレート）がミスマッチで、
   教師(Claude)が正しく「記載なし」と答えていた。
2. SFT後の「記載がありません」応答は、この分布とシステムプロンプトに忠実な挙動であり
   モデルの退行ではない。
3. **base の generation「満点」はむしろハルシネーション**（記載のない内容に引用付きで
   回答して source_exists/citation_format を満たしていた）。正解が「記載なし」のデータでは
   引用必須の eval 指標が逆効果になる。
4. analysis は微改善（date_range/query_type 各+1件）。82件・6stepでは学習量が不足。

## 技術的知見（環境・ハーネス）

- Nemotron-Nano-v2-Japanese の思考抑止は `/no_think` 無効。vLLM の
  `chat_template_kwargs {"enable_thinking": false}` のみ有効（実機確認）
- NeMo-RL は `nvcr.io/nvidia/nemo-rl:v0.6.0`（latest/v0.7.0はpull不可）。コンテナ同梱の
  /opt/nemo-rl + 構築済みvenvを使う。yaml必須キーは v0.6.0 実コード準拠（sft_lora.yaml参照）
- `chat_template: null` は「素通し」の意味。モデル既定は `"default"` 指定（重要）
- L40S 44GBでは 9B+seq4096 の活性化がOOM → seq2048。cpu_offloadはシングルGPU未実装
- g6e.xlarge は RAM 32GB でモデルロードOOM → NVMe上に swap 32G 必須
- チェックポイントは PEFT アダプタ形式 → `s4_sft/merge_lora.py` でシャード直接マージ

## 次周（ループ2）の変更 — 1変更の原則

**問い合わせテンプレートの EDINET ドメイン適合**（s2_distillation）:
チャンクの実内容（事業内容・沿革・リスク・業績等）から答えられる質問を生成するよう
テンプレートを修正 → 教師が実引用付きで回答できる generation データを作る。

付随して次回SFTで対応すべき事項:
- generation データを学習に含める（~4000字 → seq2048に収まらないため、
  チャンク数削減 or sequence_packing の実機検証が必要）
- lr 1e-4 は 82件に対して強め。2e-5〜5e-5 へ引き下げを検討
- analysis の学習量不足 → エポック増 or 蒸留件数の拡充（現状 train 82件）
