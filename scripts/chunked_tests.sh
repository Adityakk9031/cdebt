#!/usr/bin/env bash
# Run the suite in CHUNKS (groups of test files) with a cooldown between chunks, so a laptop does
# not overheat. Usage: scripts/chunked_tests.sh [cooldown_seconds] [files per chunk]
set -u
COOL="${1:-50}"; PER="${2:-6}"
cd "$(dirname "$0")/.."
FILES=(); while IFS= read -r f; do FILES+=("$f"); done < <(ls tests/test_*.py | sort)
total=${#FILES[@]}; i=0; chunk=0; fail=0
while [ $i -lt $total ]; do
  chunk=$((chunk+1)); group=("${FILES[@]:$i:$PER}"); i=$((i+PER))
  echo "chunk $chunk: ${group[*]}"
  uv run pytest -q -p no:cacheprovider "${group[@]}" 2>&1 | tail -3 | sed "s/^/  /"
  [ "${PIPESTATUS[0]}" -ne 0 ] && { fail=1; echo "  chunk $chunk FAILED"; }
  [ $i -lt $total ] && { echo "  cooling down ${COOL}s"; sleep "$COOL"; }
done
[ $fail -eq 0 ] && echo "ALL CHUNKS PASSED ($chunk chunks)" || echo "SOME CHUNKS FAILED"
exit $fail
