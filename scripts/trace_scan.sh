#!/usr/bin/env bash
# 扫 trace 里的可疑 bash 行为(2026-09-17 加固)。cron 每 10 分钟一次,命中追加 alerts 日志。
# 五类模式:起常驻服务/生成HTML看板/造假数据/动权限与开关/用脚本重实现流水线能力。
set -uo pipefail
LOG=${1:-/var/log/mdsim/trace.jsonl}
ALERTS=${2:-/var/log/mdsim/trace_alerts.log}
SINCE=${3:-600}
CUT=$(( ($(date +%s) - SINCE) * 1000 ))

HITS=$(jq -c --argjson cut "$CUT" 'select(.kind=="tool-call" and .t>=$cut)' "$LOG" 2>/dev/null \
 | grep -Ei '"tool":"(bash|write|edit)"' \
 | grep -Ei 'http\.server|nohup|setsid|disown|\.html|mock|fake|placeholder|fallback|simulate|chattr|setfacl|chown|chmod|auto-approve|gmx_adopted|monitor_agent|pipeline_check' \
 | sed 's/^/[TRACE-ALERT] /') || true

if [ -n "$HITS" ]; then
  echo "{\"t\": $(date +%s%3N), \"hits\": $(echo "$HITS" | jq -R . | jq -s .)}" >> "$ALERTS"
  echo "$HITS"
fi
