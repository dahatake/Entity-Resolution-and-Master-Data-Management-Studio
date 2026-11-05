@echo off
rem LLMマッチング実験を順に実行(ローカルLM Studioが必要)
set PYTHONIOENCODING=utf-8
set PY=.venv\Scripts\python.exe
set M=--model google/gemma-4-e4b --n 90
%PY% poc07_llm_matching.py --dataset Abt-Buy --prompt A %M% > results\log_llm1.txt 2>&1
%PY% poc07_llm_matching.py --dataset DBLP-ACM --prompt A %M% > results\log_llm2.txt 2>&1
%PY% poc07_llm_matching.py --dataset Abt-Buy --prompt B %M% > results\log_llm3.txt 2>&1
%PY% poc07_llm_matching.py --dataset Abt-Buy --prompt A --shots 6 %M% > results\log_llm4.txt 2>&1
