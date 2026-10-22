#!/usr/bin/env bash
# Run this in a SECOND terminal window while `cdebt quiz FILE` or `git push` (the pre-push hook)
# is generating questions. It shows the REAL sandbox child processes the product starts, one per
# probe, straight from the operating system. Nothing here is a demo: it only watches.
#
#   terminal 1:  bash examples/first_user/watch_sandbox.sh
#   terminal 2:  cdebt quiz pkg/pricing.py        (or: git push -u origin feature)
#
# A probe's sandbox child lives for a fraction of a second, so this polls in a tight loop.
echo "watching for cdebt sandbox processes (Ctrl-C to stop)..."
last=""
while true; do
  now="$(pgrep -fl 'cdebt-runner-.*/runner\.py' 2>/dev/null |
    sed -E 's#/private/var/folders/[^ ]*/(cdebt-runner-[^/]*)/#.../\1/#; s#/Library/Frameworks/[^ ]*/MacOS/Python#python#; s#^([0-9]+) .*python#\1 python#')"
  if [ -n "$now" ] && [ "$now" != "$last" ]; then
    printf '%s  sandbox child -> %s\n' "$(date +%T)" "$now"
  fi
  last="$now"
done
