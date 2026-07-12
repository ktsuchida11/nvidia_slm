# S3 継続事前学習 DAPT（任意）
金融コーパスでの軽い継続事前学習。**フル事前学習は予算外**。原則は「S4→S5→S6を先に一周し、
知識不足が評価で示された時だけ実施」。ただし試すこと自体は歓迎 ── **判定は必ずS6のbefore/after**で。

## 手順
```bash
make mlflow          # 学習曲線の受け皿（:5000, 閉域）
make dapt            # (1)コーパス生成は実行される (2)学習はレシピ確定後にrun_dapt.shの exit 1 を外す
make eval TAG=dapt   # base比で改善が無ければこのステージは捨てる（評価ファースト）
```
- データ: S1出力から自動生成（**資料単位で結合**→200字未満除外・図/スキャンのマーカー除外。図表入り文書の検証=TS-09）。**目安: 数千万字未満ならDAPTよりS4直行が得策**。
- 64GB現実表: 4B=フル/LoRA◎ ／ 9B=**LoRA-DAPT**（seq 2048・micro-batch 1）○。
- ロガー: MLflow(:5000)+TensorBoard 設定済み（dapt.yaml の exp_manager）。
- レシピ: NeMo Framework の継続事前学習手順を正とし、dapt.yaml のキーを合わせる
  （https://docs.nvidia.com/nemo-framework/user-guide/latest/）。K8s不要（Docker+Toolkit）。

## やってはいけない
- held-out・評価データをコーパスに混ぜる（リーク）／商用不可データを混ぜる（S1ゲート通過分のみ使う）。
