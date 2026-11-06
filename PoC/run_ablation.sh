#!/bin/bash
# Dittoの最適化アブレーション(WSL2 GPU)。Abt-Buy: 5変種x3シード。Beer(小規模): 5変種x5シード。
source ~/gv/bin/activate
cd "$(dirname "$0")"
export HF_HUB_DISABLE_SYMLINKS_WARNING=1 PYTHONIOENCODING=utf-8
for seed in 42 43 44; do
  for v in base dk su da all; do
    python -u poc15_ditto_ablation.py --dataset Abt-Buy --variant $v --seed $seed --epochs 15 --max_len 128 > results/log_abl_abt_${v}_$seed.txt 2>&1
  done
done
for seed in 42 43 44 45 46; do
  for v in base dk su da all; do
    python -u poc15_ditto_ablation.py --dataset Beer --variant $v --seed $seed --epochs 30 --max_len 128 --bs 16 > results/log_abl_beer_${v}_$seed.txt 2>&1
  done
done
echo done > results/ablation_done.txt
