#!/usr/bin/env bash
# Used by action.yml. Fetches the notes, runs the gate (re-executing recorded probes), writes a
# markdown report, posts/updates ONE sticky PR comment and a `cdebt / gate` check run (best effort),
# and exits with the gate's exit code (always 0 in advisory mode).
set -uo pipefail
PR="${PR_NUMBER:-${ISSUE_NUMBER:-}}"
HEAD_SHA="${HEAD_SHA:-$(git rev-parse HEAD 2>/dev/null || true)}"
BASE_NAME="${GITHUB_BASE_REF:-}"
if [ -z "$BASE_NAME" ] && [ -n "$PR" ] && command -v gh >/dev/null; then
  BASE_NAME=$(gh pr view "$PR" --json baseRefName --jq .baseRefName 2>/dev/null || true)
fi
BASE_REF="${CDEBT_BASE:-origin/${BASE_NAME:-main}}"
ARGS=(gate --base "$BASE_REF" --markdown cdebt-gate.md --json)
[ -n "$PR" ] && ARGS+=(--pr "$PR")
[ -n "${CDEBT_MAX_AI_LINES:-}" ] && ARGS+=(--max-ai-lines "$CDEBT_MAX_AI_LINES")
[ "${CDEBT_STRICT_UNKNOWN:-false}" = "true" ] && ARGS+=(--strict-unknown)
[ "${CDEBT_ENFORCE:-false}" = "true" ] && ARGS+=(--enforce)
[ "${CDEBT_RECHECK:-true}" = "true" ] && ARGS+=(--recheck)
# Notes are not fetched by default. Best effort, read-only: Git AI line attribution, and the
# understanding records people pushed from their pre-push hook.
git fetch --quiet origin '+refs/notes/ai:refs/notes/ai' 2>/dev/null || \
  echo "note: no refs/notes/ai on origin; attribution falls back to other sources"
git fetch --quiet origin '+refs/notes/cdebt:refs/notes/cdebt' 2>/dev/null || \
  echo "note: no refs/notes/cdebt on origin; only .cdebt/understanding.json records count"
cdebt "${ARGS[@]}" > cdebt-gate.json
RC=$?
[ -f cdebt-gate.md ] || { echo "cdebt failed (rc=$RC)"; exit "$RC"; }
cat cdebt-gate.md >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
if [ "${CDEBT_COMMENT:-true}" = "true" ] && [ -n "${GITHUB_TOKEN:-}" ] && [ -n "$PR" ] \
   && command -v gh >/dev/null; then
  ID=""
  if LIST=$(gh api "repos/${GITHUB_REPOSITORY}/issues/${PR}/comments" --paginate \
        --jq '.[] | select(.body | contains("<!-- cdebt-gate -->")) | .id' 2>/dev/null); then
    ID=$(printf '%s\n' "$LIST" | head -1)
  fi
  case "$ID" in ''|*[!0-9]*) ID="" ;; esac   # only a numeric id counts, never an error body
  if [ -n "$ID" ]; then
    gh api -X PATCH "repos/${GITHUB_REPOSITORY}/issues/comments/${ID}" -F body=@cdebt-gate.md >/dev/null || true
    # a `/cdebt recheck` comment: say so under it, so the person sees that something happened
    if [ -z "${PR_NUMBER:-}" ] && [ -n "${ISSUE_NUMBER:-}" ]; then
      TITLE=$(grep -m1 '^## ' cdebt-gate.md | sed 's/^## //')
      printf 're-checked: %s. The cdebt comment above is updated.\n' "$TITLE" > cdebt-recheck.md
      gh api -X POST "repos/${GITHUB_REPOSITORY}/issues/${PR}/comments" -F body=@cdebt-recheck.md >/dev/null || true
    fi
  else
    gh api -X POST "repos/${GITHUB_REPOSITORY}/issues/${PR}/comments" -F body=@cdebt-gate.md >/dev/null || true
  fi
fi
# The explicit check run: success when everything is understood; otherwise failure when enforcing
# and NEUTRAL when advisory (a job cannot be neutral, a check run can). Best effort: a read-only
# token (a fork PR) cannot create it and the job result is unaffected.
if [ -n "${GITHUB_TOKEN:-}" ] && [ -n "${HEAD_SHA:-}" ] && command -v gh >/dev/null && command -v jq >/dev/null; then
  PASSED=$(jq -r .passed cdebt-gate.json 2>/dev/null || echo true)
  N=$(jq -r .ai_lines_unverified cdebt-gate.json 2>/dev/null || echo 0)
  if [ "$PASSED" = "true" ]; then CONC=success
  elif [ "${CDEBT_ENFORCE:-false}" = "true" ]; then CONC=failure
  else CONC=neutral; fi
  gh api -X POST "repos/${GITHUB_REPOSITORY}/check-runs" \
    -f name="cdebt / gate" -f head_sha="$HEAD_SHA" -f status=completed -f conclusion="$CONC" \
    -f "output[title]=cdebt: ${N} AI-written lines not understood" \
    -F "output[summary]=@cdebt-gate.md" >/dev/null 2>&1 || \
    echo "note: could not create the check run (read-only token?)"
fi
exit "$RC"
