#!/bin/bash
# 每个交易日 15:45（北京时间）由 qishi-daily.timer 触发：结算 → 计划 → 周五进化 → 重建看板
set -u
cd "$(dirname "$0")/../engine"
LOG=../reports/job_$(TZ=Asia/Shanghai date +%F).log
{
  echo "== $(TZ=Asia/Shanghai date '+%F %T') run_day"
  python3 run_day.py
  if [ "$(TZ=Asia/Shanghai date +%u)" = "5" ]; then
    echo "== evolve"; python3 evolve.py && python3 daily.py
  fi
  python3 site.py
  python3 notify.py
} >> "$LOG" 2>&1
