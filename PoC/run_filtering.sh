#!/bin/bash
source ~/gv/bin/activate
cd "$(dirname "$0")"
export HF_HUB_DISABLE_SYMLINKS_WARNING=1 PYTHONIOENCODING=utf-8
python -u poc18_filtering_benchmark.py > results/log_poc18.txt 2>&1
echo done > results/filtering_done.txt
