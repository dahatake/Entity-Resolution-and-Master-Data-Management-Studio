#!/bin/bash
# GPU(WSL2 Ubuntu aarch64 + CUDA 13)での微調整。論文設定に近づける: RoBERTa-base, lr 3e-5, bs 32, 訓練全量。
source ~/gv/bin/activate
cd "$(dirname "$0")"
export HF_HUB_DISABLE_SYMLINKS_WARNING=1 PYTHONIOENCODING=utf-8
run() { python -u poc06_plm_finetune.py "$@" > "results/log_gpu_$TAG.txt" 2>&1; }
TAG=dblp_rob     run --dataset DBLP-ACM --model roberta-base --epochs 10 --max_len 128 --valid_n 2473 --seed 42 --tag _gpu10ep
TAG=ag_rob       run --dataset Amazon-Google --model roberta-base --epochs 15 --max_len 128 --valid_n 2293 --seed 42 --tag _gpu15ep
TAG=wdc_rob      run --dataset WDC --model roberta-base --epochs 20 --max_len 128 --valid_n 2500 --seed 42 --tag _gpu20ep
TAG=abt_dist     run --dataset Abt-Buy --model distilbert-base-uncased --epochs 15 --max_len 256 --valid_n 1916 --seed 42 --tag _gpu15ep
TAG=abt_rob_s43  run --dataset Abt-Buy --model roberta-base --epochs 15 --max_len 256 --valid_n 1916 --seed 43 --tag _gpu15ep
TAG=abt_rob_lr   run --dataset Abt-Buy --model roberta-base --epochs 15 --max_len 256 --valid_n 1916 --seed 42 --lr 1e-5 --tag _gpu15ep_lr1e-5
echo done > results/gpu_done.txt
