# PoC

`work/IssueAnalyticsReport.md` の主張を小さなデータとプログラムで検証する。結果と考察は `work/PoC-ResultAnalyticsReport.md`。

| ファイル | 検証内容 | データ |
|---|---|---|
| `poc01_accuracy_and_blocking.py` | Accuracyの無意味さ、ブロッキングの再現率の天井 | `data/fake_1000.csv`(Splink公開データ。`cluster`列が正解) |
| `poc02_splink_vs_rules.py` | Splink(教師なしEM)と決定的ルール、連結成分による推移閉包の危険 | 同上 |
| `poc03_japanese_normalization.py` | 日本語の法人名・住所の正規化の効果と限界 | 合成(乱数シード固定、`results/poc03_synthetic_companies.csv`) |
| `poc04_evaluation_design.py` | ラベル数と信頼区間幅、誤統合コスト比と最適閾値 | poc02の出力 |
| `poc05_scaling.py` | 9千〜9万件での所要時間の傾向(引数に件数を渡すと100万件版: `poc05_scaling.py 230000 770000`) | 合成(乱数シード固定) |
| `poc06_plm_finetune.py` | Ditto型PLM微調整(DistilBERT)とTF-IDFの比較 | `data/{DBLP-ACM,Abt-Buy,Amazon-Google}`(Ditto公式リポジトリ megagonlabs/ditto の `data/er_magellan/`)、`data/WDC` |
| `poc07_llm_matching.py`, `run_llm.cmd` | ローカルLLM(LM Studio、`localhost:1234`)によるマッチング | 同上 |
| `poc08_embedding_blocking.py`, `poc08b_embedding_matching.py` | 埋め込みによるブロッキング/教師なしマッチング | 同上(nomic-embedはLM Studio、他はHugging Face公開重み) |
| `poc10_wdc_products.py` | WDC Productsの漏洩点検・古典分類器・Ditto形式への変換 | `data/wdc/80pair/`(下記から取得) |
| `poc11_auto_merge_review_queue.py` | 自動マージ/確認キュー運用のシミュレーション | poc02の出力 |
| `poc12_llm_prompt_sensitivity.py` | 3つのローカルLLM×3プロンプト×2データの感度(再開可能) | Ditto分割 |
| `poc14_splink_sensitivity.py` | Splink教師なし学習の感度(シード×再現率の仮定) | fake_1000 |
| `run_gpu.sh`, `run_gpu_frac.sh` | GPU微調整(RoBERTa-base、全量/データ割合) | 同上、WDC |
| `poc15_ditto_ablation.py`, `poc15b_ablation_aggregate.py`, `run_ablation.sh` | Dittoの3最適化(DK/SU/DA)の簡易再実装とアブレーション | Abt-Buy、Beer(`data/Beer`はDitto公式から取得) |
| `poc16_wdc_relabel_prepare.py`, `poc16b_wdc_local_judge.py`, `poc16c_wdc_relabel_analyze.py` | WDCのラベル再評価(層化600組、ローカル判定者、集計) | `data/wdc/{80,50,20}pair/` |
| `poc17_hosted_prepare.py` | ホスト型LLM評価用の標本(ラベルなし)。判定自体はサブエージェント経由 | Ditto分割 |
| `poc18_filtering_benchmark.py`, `run_filtering.sh` | フィルタリング(ブロッキング/NN結合)の縮小ベンチ | Ditto分割(全ペアから再構成) |

`poc03` は環境変数 `POC_SEED` で生成シードを変更できる(複数シード検証)。

## GPU(WSL2)
Windows ARM64用のCUDA版PyTorchはない。WSL2のUbuntu(aarch64)から同じGPUが見える。
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh; source ~/.local/bin/env
uv venv --python 3.12 ~/gv; source ~/gv/bin/activate
uv pip install torch --index-url https://download.pytorch.org/whl/cu130
uv pip install numpy transformers scikit-learn scipy pandas rapidfuzz
setsid nohup bash run_gpu.sh > results/run_gpu.out 2>&1 < /dev/null &
```
`poc06_plm_finetune.py` はCUDAがあれば自動でbf16のGPU学習になる。

`data/wdc/` は容量のためgit管理外。`https://data.dws.informatik.uni-mannheim.de/largescaleproductcorpus/data/wdc-products/80pair.zip` を `data/wdc/` に展開する。
Ditto分割は `https://raw.githubusercontent.com/megagonlabs/ditto/master/data/er_magellan/{Textual/Abt-Buy,Structured/DBLP-ACM,Structured/Amazon-Google}/{train,valid,test}.txt` から取得。

## 再現手順
Windows ARM64では `pyarrow` のARM64 wheelがないため、x64版Python 3.12のvenvを使った(x64/ARM64 Windows以外ならそのままのPythonで可)。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe poc01_accuracy_and_blocking.py
.\.venv\Scripts\python.exe poc02_splink_vs_rules.py   # poc04の前に実行
.\.venv\Scripts\python.exe poc03_japanese_normalization.py
.\.venv\Scripts\python.exe poc04_evaluation_design.py
.\.venv\Scripts\python.exe poc05_scaling.py
```

出力は `results/` に保存される。`data/fake_1000*.csv` は
https://github.com/moj-analytical-services/splink_datasets の `data/` から取得したもの。

## PRDの可視化(参照プロトタイプ)
`PoC/build_prd_visuals.py` が、`PoC/results/` と `work/prd.md` から `work/prd-visuals/data.js` を生成する(27件の整合性検査つき。PoC-02/04の報告値との一致、タスクの依存の非循環、要求とタスクの網羅)。表示は `work/prd-visuals/index.html`(d3.js v7同梱、ネットワーク不要)。
```powershell
PoC\.venv\Scripts\python.exe PoC\build_prd_visuals.py
python -m http.server 8765 -d work\prd-visuals   # http://127.0.0.1:8765/ を開く
```

