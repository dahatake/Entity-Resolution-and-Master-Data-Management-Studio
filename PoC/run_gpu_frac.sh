#!/bin/bash
# データ効率: Abt-BuyでRoBERTa-baseの訓練データ割合を変える(Dittoの「半分のラベルで従来SOTA並み」の方向の確認)
source ~/gv/bin/activate
cd "$(dirname "$0")"
export HF_HUB_DISABLE_SYMLINKS_WARNING=1 PYTHONIOENCODING=utf-8
for f in 0.05 0.1 0.25 0.5; do
  python -u poc06_plm_finetune.py --dataset Abt-Buy --model roberta-base --epochs 15 --max_len 256 --valid_n 1916 --seed 42 --train_frac $f --tag _gpu15ep_frac$f > results/log_gpu_frac$f.txt 2>&1
done
echo done > results/gpu_frac_done.txt
