# リモートGPUノード運用手順（loop8-10で確立）

学習・配信は AWS spot ノード（g6e系・L40S、Terraform管理・専用アカウント）。
ローカル（DevContainer/Mac）は実装・テスト・$0配管検証まで。インフラ詳細・再開手順はユーザーのメモリ
（nvidia-slm-gpu-infra）と terraform/ が正。

## ノードのライフサイクル

- **起動**: ユーザー承認後（billing-gates.md）。起動は Mac 側から（aws ec2 start-instances / terraform）。
  SSO トークン切れは `aws sso login --profile gpu-account` を新しいシェルで
- **停止**: ループの区切りごとに必ず停止を確認する（「ノード撤収」まで含めてループのクローズ）。
  spot なので中断もあり得る前提で save_period / ckpt を設計する
- **セッション**: 学習は必ず **tmux 内**で `2>&1 | tee /tmp/xxx.log`。SSM切断後は tail/grep でログ回収。
  `docker run -d`（serve系）はデタッチなので切断でも生存

## 同期（S3 二系統）

```
Mac:   remote/push-to-s3.sh --with-repo   # ①repo（コード） ②data/*（学習データ）の二系統
ノード: pull系スクリプトで repo + data を sync
```

- **`--with-repo` を忘れるとノード側の Makefile / スクリプトが古いまま走る**（実害あり）。
  コード変更を伴うときは必ず付け、ノード側で該当ファイルの変更が届いたことを確認してから実行
- 成果物（ckpt・results）はノード→S3 へ。**EBS は消える前提**（旧ノードEBS消失で結果を会話記録から復元した前例あり）。
  評価結果 json・学習ログは出たらすぐ S3 か PR に固定する

## チェックポイント運用

- **新規走行前に checkpoint_dir を退避**（`mv checkpoints/sft checkpoints/sft-<tag>`）。
  残っていると自動レジュームを試みて死ぬ（traps.md）。DAPT側は resume=None 固定にしてある
- **保持は直近2世代**を目安に、S3 バックアップ確認後に古いものを削除（削除はユーザー確認must。
  コンテナ root 所有なので sudo rm）
- NeMo automodel の ckpt は `<name>/hf_weights/` に **HF形式で直接保存**される（変換工程不要）。
  配信前に config.json の architectures を確認（traps.md §vLLM）

## ディスク（長走前チェック must）

- `df -h` で余裕確認。**ディスクフルはハング→NCCLタイムアウトの偽装**になる
- 目安: nemo:25.04 展開 ~60GB / DAPT ckpt ~50-72GB（top_k1+last）/ nemo-rl・vllm イメージ各数十GB
- 空け方: 旧イメージ `docker rmi`（**rmi 後に `docker image ls` で実際に消えたか確認** — 効いていなかった前例あり）、
  旧 ckpt は S3 退避→sudo rm

## ジョブの停止・監視

- `make ... | tee` は **Ctrl-C でコンテナが死なず GPU 占有が残る**。停止は
  `docker rm -f $(docker ps -qf ancestor=<学習イメージ>)`。停止・完走後は `nvidia-smi` でゾンビ確認
- step 表示は **epoch 毎にリセット**される（`Step N/118` → 1 に戻る）。再起動と誤認しない
  （グローバルstep は checkpoint 名・コンテナ Up 時間で判断）
- NCCL watchdog タイムアウトが出たら: dmesg（Xid）とディスクを確認 → クリーンなら一過性として再走提案
- ノードへのスクリプト転送は heredoc 貼り付けで行頭文字が欠ける事故あり →
  `cat > file` 転送 + `python -m py_compile` 検証の2段構え

## PR規律（リポジトリ運用）

- 作業は必ずブランチ + 小さめPR。lint / format / テストを通してから push
- **マージ依頼は全コミット push 完了後**（must）。マージ後のブランチへの追い push は宙吊りになる
  （スタックPR罠 — loop9/10 で2回再発し、回収PRが必要になった）
- ループの実装は「plan doc PR → 実装PR（機能単位） → report PR」の流れ。実機修正はその都度コミットし、
  走行と並行してPR化する
