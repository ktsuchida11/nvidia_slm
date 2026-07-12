# S7 ガードレール作成・検証（NeMo Guardrails library）
config/config.yml（input/retrieval/outputレール+self-checkプロンプト）+ config/rails.co（Colangトピックレール）。
```bash
make guardrails        # レール適用済みエンドポイント :8100（K8s不要・Dockerのみ）
make guardrails-test   # nemoguardrails evaluate + garak(promptinject) → results/garak*
```
段階導入: まずself-checkで開始 → NemoGuard安全モデル(Content Safety 8B等, 要GPU)をmodels:に追加して強化。
検証はdocs/20-test-scenarios.md TS-07(before/after)/TS-08(手動4種)。
**将来K8s(microservices)へ移行する時も、このconfigがそのまま使える**のが library で作る利点。

## 補完：出口ガバナンス（任意）
会話レール（本S7）に加え、通信/実行/認証情報を**アプリ外(プロセス外)で**封じ込める **OpenShell** を足せる（二層防御）。
クローズド網の出口集中管理・認証情報分離・監査に有効だが現状アルファ。設計は `docs/07-openshell-egress-governance.md`。
