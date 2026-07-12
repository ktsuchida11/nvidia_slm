# S1 収集・管理（NeMo Curator流）— 実装済
入力: `/data/raw/*.jsonl|*.txt` → 出力: `/data/curated/{curated,rejected}.jsonl + stats.json`
処理: NFKC正規化 / 品質スコア(日本語比率・記号率・反復) / PIIマスク(メール・電話等) /
**ライセンスゲート**(商用可のみ: Apache/MIT/CC-BY/CC-BY-SA/CC0/PDL-1.0/gov-jp/own/synthetic。**NC/ND/JaFInは拒否**) / exact + **fuzzy(4-gram MinHash, 閾値0.75実測校正)** 重複排除 / domain分類。
```bash
make fetch REPO=<HF dataset id> LICENSE=<カード記載>   # 金融データ取得(fetch_dataset.py, 商用可のみ)
make curate                                            # 実行（CPUで可・K8s不要）
python s1_curate.py --engine curator ...               # Curatorにexact dedup委譲（不可時自動fallback）
```
大規模(数百万件)になったらCuratorのGPU/Dask構成へ: https://docs.nvidia.com/nemo/curator/
