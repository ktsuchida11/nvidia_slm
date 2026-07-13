# 09. この開発環境（DevContainer + Docker outside of Docker）での実行メモ

このリポを **セキュアDevContainer**（`/workspace`、firewall・sandbox有効）内の Claude Code で
運転する際の環境固有の事実と対処を記録する。汎用ドキュメントではなく **この環境の実測記録**。

## 構成の事実

| 項目 | 実測値 |
|---|---|
| CPU/OS | aarch64 (Apple Silicon) / Linux DevContainer |
| Docker | ホスト(macOS)の Docker Desktop に `tecnativa/docker-socket-proxy`(:2375) 経由で接続（DooD） |
| GPU | なし（S3/S4/S5 はリモートGPUホストで実行） |
| パス対応 | `/workspace` ⇔ ホスト `/Users/tsuchitakouji/pg_work_space/nvidia_slm/nvidia_slm/workspace` |

## DooD の帰結と対処

1. **`-v` のソースはホスト側パスが必要**（コンテナ内パスは "mounts denied"）。
   → Makefile は `HOST_PWD`（既定 `$(PWD)`、通常環境では挙動不変）でマウント元を指定。
   この環境では `hostpath.mk`（gitignore済・機械固有）が `HOST_PWD` を上書きする。
2. **`-p 127.0.0.1:PORT` は macOSホスト側に公開される**。DevContainer内の `localhost:PORT` からは
   届かない。MLflow(:5000)等のUIは **macOS側のブラウザ** で開く。コンテナ間通信が必要な場合は
   共有dockerネットワーク＋サービス名で接続する。
3. **TTYなし**: `docker run -it` は失敗するため Makefile は `-i` に変更済み。
4. **このホストの Docker Desktop では `*-slim` の arm64 イメージが `exec: input/output error` になる**
   （python:3.12-slim / 3.13-slim で再現。非slim `python:3.12-bookworm` と debian, hello-world は正常）。
   → `hostpath.mk` で `PY_IMG := python:3.12-bookworm`、`TOOLS_BASE := python:3.12-bookworm` を設定済み。
5. **docker-socket-proxy が落ちると docker CLI が全滅する**（`lookup docker-proxy ... no such host`）。
   復旧はコンテナ内からは不可能。**macOSホスト側のターミナル**で:
   ```bash
   cd <devcontainerプロジェクト> && docker compose up -d docker-proxy
   # または VS Code: Dev Containers: Rebuild and Reopen in Container
   ```

## 検証済みの動作（2026-07-13）

```bash
make setup                                    # OK
# docs/20 TS-01〜03 サンプル生成 → data/raw/sample.jsonl
make curate       # kept=2 rejected=3 fuzzy_dropped=1 pii_masked={EMAIL,PHONE_JP} → TS-01/02/03 合格
make distill-dry  # total=301 train=242/valid=29/heldout=30, rejects={} → TS-04 合格
make eval-dry     # "pass": true 全指標1.0 exit 0 → TS-06 合格
```

## この環境での役割分担

| ステージ | 実行場所 | 備考 |
|---|---|---|
| S1 curate / S2 distill(-dry) / S3 prep_corpus / S6 eval(-dry) | この環境（CPUコンテナ） | `PY_IMG=python:3.12-bookworm` or `nemo-tools:latest` |
| S2 distill 本番 | この環境 | 要 `ANTHROPIC_API_KEY`（承認ゲート） |
| S3 DAPT / S4 SFT / S5 GRPO / vLLM配信 | リモートGPUホスト | `remote/setup-gpu-host.sh` 参照。NGCイメージは x86_64 |
| MLflow / TensorBoard | この環境で起動 → macOS側ブラウザで閲覧 | ポート公開はホスト側になるため |
| S7 guardrails / garak | この環境（CPUコンテナ）+ 推論先はGPUホスト | |

## リモートGPUホストとのデータ受け渡し

このDevContainerは `ssh/scp/curl` を遮断しているため、**転送は macOSホスト側ターミナルから**行う:

```bash
# 学習データを送る（heldout は送らない運用も可）
WS=/Users/tsuchitakouji/pg_work_space/nvidia_slm/nvidia_slm/workspace/nvidia_slm/nemo-pipeline
scp $WS/data/distilled/{train,valid}.jsonl gpu-host:~/nvidia_slm/nemo-pipeline/data/distilled/

# チェックポイント/評価結果を回収する
scp -r gpu-host:~/nvidia_slm/nemo-pipeline/checkpoints $WS/
scp -r 'gpu-host:~/nvidia_slm/nemo-pipeline/results/eval_*.json' $WS/results/
```

git リポ（GitHub private）経由の同期でも良い（データはサイズと機密性に注意。`data/` は gitignore 済）。
