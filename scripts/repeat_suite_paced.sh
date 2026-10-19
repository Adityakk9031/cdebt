#!/usr/bin/env bash
# Gentle flake gate: run the full suite runs FIRST..LAST (default 10..20) in batches of BATCH
# (default 2) with PAUSE seconds (default 50) between batches so the machine can cool down.
# The final run is separated from the previous batch by an extra FINAL_PAUSE (default 120 s).
# Stops at the first failure. Usage: repeat_suite_paced.sh [FIRST LAST BATCH PAUSE FINAL_PAUSE]
set -uo pipefail
FIRST="${1:-10}"; LAST="${2:-20}"; BATCH="${3:-2}"; PAUSE="${4:-50}"; FINAL_PAUSE="${5:-120}"
pass=0; in_batch=0
for i in $(seq "$FIRST" "$LAST"); do
  if [ "$i" = "$LAST" ] && [ "$in_batch" -eq 0 ] && [ "$LAST" -gt "$FIRST" ]; then
    echo "pause ${FINAL_PAUSE}s before the final run"; sleep "$FINAL_PAUSE"
  fi
  s=$(date +%s)
  if out=$(uv run pytest -q -p no:cacheprovider 2>&1); then
    pass=$((pass+1)); in_batch=$((in_batch+1))
    echo "run $i/$LAST: PASS ($(( $(date +%s)-s ))s) $(echo "$out" | tail -1)"
  else
    echo "run $i/$LAST: FAIL"; echo "$out" | tail -30; exit 1
  fi
  if [ "$in_batch" -ge "$BATCH" ] && [ "$i" -lt "$LAST" ]; then
    in_batch=0
    if [ "$i" -lt $((LAST-1)) ]; then echo "batch done; cooling down ${PAUSE}s"; sleep "$PAUSE"; fi
  fi
done
echo "ALL $pass RUNS ($FIRST..$LAST) PASSED"
