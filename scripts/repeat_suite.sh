#!/usr/bin/env bash
# Run the whole suite N times in a row (default 20); stop at the first failure. Zero flakes required.
set -uo pipefail
N="${1:-20}"; pass=0
for i in $(seq 1 "$N"); do
  s=$(date +%s)
  if out=$(uv run pytest -q -p no:cacheprovider 2>&1); then
    pass=$((pass+1)); echo "run $i/$N: PASS ($(( $(date +%s)-s ))s) $(echo "$out" | tail -1)"
  else
    echo "run $i/$N: FAIL"; echo "$out" | tail -30; exit 1
  fi
done
echo "ALL $pass/$N RUNS PASSED"
