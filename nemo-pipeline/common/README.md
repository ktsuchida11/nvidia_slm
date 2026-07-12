# common — 共有コード
- `reward.py` : 検証可能報酬（source_exists / citation_format / penalty）。**S2品質ゲート・S5報酬・S6評価で同一関数を共有**（単一の忠実性概念）。
- `schema.py` : Stage1構造化JSONの検証（S2教師ラベル / S6被評価出力）。
- `prompts.py`: 教師・評価プロンプト（S2/S6で同一物を使い条件を揃える）。
変更時は TS-04/05/06(dry) を再実行。
