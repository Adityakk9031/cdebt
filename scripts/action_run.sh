#!/usr/bin/env bash
# Used by action.yml. Runs the gate, writes a markdown report, posts/updates ONE sticky PR
# comment (best effort), and exits with the gate's exit code.
set -uo pipefail
BASE_REF="${CDEBT_BASE:-origin/${GITHUB_BASE_REF:-main}}"
ARGS=(gate --base "$BASE_REF" --markdown cdebt-gate.md --json)
[ -n "${CDEBT_MAX_AI_LINES:-}" ] && ARGS+=(--max-ai-lines "$CDEBT_MAX_AI_LINES")
[ "${CDEBT_STRICT_UNKNOWN:-false}" = "true" ] && ARGS+=(--strict-unknown)
# Git AI notes are not fetched by default; best effort, read-only.
git fetch --quiet origin '+refs/notes/ai:refs/notes/ai' 2>/dev/null || \
  echo "note: no refs/notes/ai on origin; attribution falls back to other sources"
cdebt "${ARGS[@]}" > cdebt-gate.json
RC=$?
[ -f cdebt-gate.md ] || { echo "cdebt failed (rc=$RC)"; exit "$RC"; }
cat cdebt-gate.md >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
if [ "${CDEBT_COMMENT:-true}" = "true" ] && [ -n "${GITHUB_TOKEN:-}" ] && [ -n "${PR_NUMBER:-}" ] \
   && command -v gh >/dev/null; then
  ID=$(gh api "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" --paginate \
        --jq '.[] | select(.body | contains("<!-- cdebt-gate -->")) | .id' | head -1)
  if [ -n "$ID" ]; then
    gh api -X PATCH "repos/${GITHUB_REPOSITORY}/issues/comments/${ID}" -F body=@cdebt-gate.md >/dev/null || true
  else
    gh api -X POST "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" -F body=@cdebt-gate.md >/dev/null || true
  fi
fi
exit "$RC"
