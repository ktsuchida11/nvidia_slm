# templates — データセット/メタデータ作成テンプレート

文書から学習データを作る際に流用するテンプレート集。使い方は `docs/04-dataset-templates.md`、
独自データ取込の設計は `docs/05-proprietary-data-design.md`。

| ファイル | 用途 |
|---|---|
| `record.schema.json` | 1学習例のJSON Schema（指示＋来歴メタ）。商用可ライセンス・allowed_useを型で強制 |
| `dataset_card.md` | データセットカード（人間可読・Datasheets/Data Cards準拠） |
| `croissant.metadata.json` | Croissant（機械可読・MLCommons標準・HF採用）最小スケルトン |
| `source_manifest.yaml` | 独自データ取込マニフェスト＝データ契約（承認・権利・PII・用途・保持） |

検証例:
```bash
python -c "import json,jsonschema,sys; jsonschema.Draft202012Validator.check_schema(json.load(open('record.schema.json')))"
python -c "import yaml; yaml.safe_load(open('source_manifest.yaml'))"
```
