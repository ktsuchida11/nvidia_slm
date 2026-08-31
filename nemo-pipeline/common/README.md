# common — 共有コード

**同じ概念を複数ステージで共有するための置き場。** ここを変えると S2 / S5 / S6 が同時に動くので、
変更時は必ずテストを回すこと。

| ファイル | 中身 | 使う場所 |
| --- | --- | --- |
| `reward.py` | 検証可能報酬（`source_exists` / `citation_format` / penalty） | **S2 品質ゲート・S5 報酬・S6 評価で同一関数**（＝単一の忠実性概念） |
| `schema.py` | クエリ解析の構造化JSON検証 | S2 の教師ラベル検証 / S6 の被評価出力検証 |
| `prompts.py` | 教師・評価プロンプト | S2 と S6 で同一物を使い条件を揃える |
| `finqa.py` | 数値QAの採点（`parse_number` ほか。許容誤差つき） | S2 finqa データ生成 / S6 finqa 評価 |
| `label_lint.py` | 教師ラベルの機械チェック | S2 蒸留後の `make lint-labels` |

## なぜ共有するか

S2（データを作る）・S5（報酬を与える）・S6（採点する）で**別々の実装を持つと、
「学習で報われる書き方」と「評価で点が入る書き方」がずれる**。
loop8 の敗因の一つが学習と評価の条件不一致だったので、ここは意図的に1箇所に寄せてある。

## 変更したら

```bash
for t in tests/test_*.py; do python3 "$t"; done   # pytest 不要・11ファイル
```

`reward.py` / `schema.py` を触ったときは、加えて配管も通す:

```bash
make distill-dry eval-dry
```
