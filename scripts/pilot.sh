#!/usr/bin/env bash
# Reproduces docs/CASE_STUDIES.md: clones 3 public repos that use AI coding agents (pinned to
# the HEAD at clone time, recorded in heads.txt) and runs map / gate / plan / index / quiz.
# Usage: scripts/pilot.sh WORKDIR   (outputs go to docs/case_studies/)
set -uo pipefail
W="${1:?workdir}"; OUT="$(cd "$(dirname "$0")/.." && pwd)/docs/case_studies"; mkdir -p "$W" "$OUT"
CD="uv run --project $(cd "$(dirname "$0")/.." && pwd) cdebt"
t() { local s=$(date +%s); "$@"; local rc=$?; echo "$(( $(date +%s)-s ))s rc=$rc" >&2; return $rc; }
: > "$OUT/heads.txt"; : > "$OUT/timings.txt"
for r in tachyon-beep/space_chassis lucascaro/hive Popoboxxo/agent-meta; do
  d=$(basename $r); [ -d "$W/$d" ] || git clone -q https://github.com/$r "$W/$d"
  echo "$r $(git -C $W/$d rev-parse HEAD) $(git -C $W/$d log -1 --format=%ci)" >> "$OUT/heads.txt"
  { echo "== $d"; 
    ( time $CD map --repo $W/$d --no-scip --json > $OUT/$d.map.json ) 2>&1 | grep real | sed 's/^/map /'
    ( time $CD gate --repo $W/$d --base HEAD~25 --json > $OUT/$d.gate.json ) 2>&1 | grep real | sed 's/^/gate /'
    ( time $CD plan --repo $W/$d --no-scip --top 8 --json > $OUT/$d.plan.json ) 2>&1 | grep real | sed 's/^/plan /'
  } >> "$OUT/timings.txt"
done
d=agent-meta
{ echo "== $d scip"; ( time $CD index --repo $W/$d ) 2>&1 | grep real | sed 's/^/index /'
  ( time $CD map --repo $W/$d --json > $OUT/$d.map-scip.json ) 2>&1 | grep real | sed 's/^/map-scip /'; } >> "$OUT/timings.txt"
echo done
