# loop14 実機ランブック — rerank NIM ゲートA/B

計画: docs/loop-14-plan.md（PR #70）/ 実装: PR #71（マージ済み）。
**①のノード起動から GPU 課金開始**（g6e.12xlarge spot ~$4/h × 1.5-2.5h ≈ **$6-10**。学習・教師APIなし）。

判定値: **probe_acc ≥ 0.55 / gold_in_ctx_rate ≥ 0.75**（loop13 の k=10 実測 0.5427 / 0.7387 を top5 のまま超えること）。
`gold_in_k0_rate`（新指標）= rerank の理論天井。

## ① Mac: コード同期 + ノード起動

```bash
# 素のシェルで（.env を source したシェルは gpu-account プロファイルを見失う罠）
cd <repoルート> && bash nemo-pipeline/remote/push-to-s3.sh --with-repo
aws ec2 start-instances --instance-ids i-0e2549759e6e56b5c --profile gpu-account
# 数分後、terraform output -raw connect_gpu のコマンドに --profile gpu-account を付けて接続
# 接続後は必ず: sudo su - ubuntu
```

## ② ノード: 起動ルーチン + 同期確認

```bash
bash /opt/nvidia_slm/nvidia_slm/nemo-pipeline/remote/node-start.sh
cd /opt/nvidia_slm/nvidia_slm/nemo-pipeline
grep -c serve-rerank Makefile      # 2以上 = loop14コードが届いている
ls s8_retrieval/reranker.py data/retriever/meta.json data/pretrain/probe_qa.jsonl
df -h /                             # rerank NIMイメージ分 ~15GB の余裕確認
```

## ③ ノード: 配信3本起動（GPUピン留め: embed=0 / vLLM=1 / rerank=2）

```bash
echo "$NGC_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin
make serve-embed GPU='--gpus device=0'
make serve-rerank GPU='--gpus device=2'    # 初回はイメージpull（~10-15分）
docker rm -f llm-gen 2>/dev/null; make grounded-serve GPU='--gpus device=1'   # sft13 :8002
# 待機: docker logs -f rerank-nim → "Uvicorn running"
#       docker logs -f llm-gen   → "Application startup complete"
```

既知の罠（loop12 の NIM 3点セット）:

- `Access Denied` → docker login nvcr.io 忘れ（ユーザー名は文字列 `$oauthtoken` そのもの）
- `Permission denied (os error 13)` → `sudo chown -R 1000:1000 ~/.cache/nim`
- vLLM が `Free memory < utilization` → GPU ピン留め漏れ（全サービス `GPU='--gpus device=N'` 指定）

## ④ ノード: ゲートA smoke（疎通確認 — 出力を Claude に貼る）

```bash
curl -s http://127.0.0.1:8003/v1/ranking -H 'Content-Type: application/json' -d \
 '{"model":"nvidia/llama-3.2-nv-rerankqa-1b-v2","query":{"text":"売上高はいくらか"},"passages":[{"text":"当期の売上高は1,485億円"},{"text":"本日は晴天なり"}],"truncate":"END"}'
```

期待: `rankings` の先頭が `"index":0`（売上高の文が上位）。確認できたら⑤へ。

## ⑤ ノード: ゲートB 本評価（tmux 内・各~15-20分）

```bash
tmux new -s rr    # ブリッジIPは必ず tmux セッション内で取り直す（毎回の罠）
EMB_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' embed-nim)
RR_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' rerank-nim)
GEN_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' llm-gen)
export OPENAI_BASE_URL=http://$GEN_IP:8000/v1 EMBED_BASE_URL=http://$EMB_IP:8000/v1 \
       RERANK_BASE_URL=http://$RR_IP:8000/v1 EVAL_CHAT_KWARGS='{"enable_thinking": false}'
cd /opt/nvidia_slm/nvidia_slm/nemo-pipeline

make rag-eval TAG=rag199_sft13_rr50  RERANK=1 RERANK_K0=50    # 主測定: k0=50→top5
make rag-eval TAG=rag199_sft13_rr100 RERANK=1 RERANK_K0=100   # k0の効き確認
```

各 run 完了ごとに report JSON（`probe_acc` / `gold_in_ctx_rate` / `gold_in_k0_rate`）を Claude に貼る。
結果次第で3本目: `make rag-eval RAG_K=10 RERANK=1 RERANK_K0=100 TAG=rag199_sft13_rr100k10`

## ⑥ ノード: S3退避 → Mac: 停止

```bash
aws s3 sync results/ s3://$CKPT_BUCKET/results/loop14/ --exclude "*" --include "eval_probe_rag199_sft13_rr*"
```

```bash
# Mac から:
aws ec2 stop-instances --instance-ids i-0e2549759e6e56b5c --profile gpu-account
```
