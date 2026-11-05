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
