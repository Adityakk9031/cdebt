#!/usr/bin/env bash
# Regenerates tests/fixtures/gitai/* from REAL git-ai output (v1.7.5 verified).
# Uses trace2 env vars for this process only; touches no global git config.
set -euo pipefail
OUT="$(cd "$(dirname "$0")/.." && pwd)/tests/fixtures/gitai"
export PATH="$HOME/.git-ai/bin:$PATH"
export GIT_TRACE2_EVENT="af_unix:stream:$HOME/.git-ai/internal/daemon/trace2.sock" GIT_TRACE2_EVENT_NESTING=0
W="$(mktemp -d)"; cd "$W"
git init -q -b main; git config user.email dev@example.com; git config user.name Dev
printf 'def human(x):\n    return x + 1\n' > a.py
printf 'one\ntwo\nthree\n' > b.py
git add .; git-ai checkpoint human a.py b.py; git commit -qm "c1 human"
# c2: AI appends two functions to a.py, and inserts middle lines in b.py
printf 'def ai_fn(x):\n    return x * 2\n\ndef ai_two(y):\n    return y - 1\n' >> a.py
printf 'one\nAI-A\nAI-B\ntwo\nthree\nAI-C\n' > b.py
git-ai checkpoint mock_ai a.py b.py; git commit -qam "c2 ai"
# c3: human edits one AI line, adds a human line
sed -i.bak 's/return x \* 2/return x * 3/' a.py; rm a.py.bak; printf 'tail = 1\n' >> a.py
git-ai checkpoint human a.py; git commit -qam "c3 human edits AI line"
git-ai await
mkdir -p "$OUT"
git log --format='%H %s' > "$OUT/log.txt"
for c in $(git rev-list --reverse HEAD); do
  git notes --ref=ai show "$c" > "$OUT/note_$(git log -1 --format=%s "$c" | cut -d' ' -f1).txt" 2>/dev/null || true
  git-ai stats "$c" --json > "$OUT/stats_$(git log -1 --format=%s "$c" | cut -d' ' -f1).json"
done
git blame --porcelain a.py > "$OUT/blame_a.porcelain"; git blame --porcelain b.py > "$OUT/blame_b.porcelain"
git-ai blame a.py > "$OUT/gitai_blame_a.txt" 2>&1 || true
cp a.py "$OUT/final_a.py"; cp b.py "$OUT/final_b.py"
git bundle create "$OUT/repo.bundle" --all --quiet 2>/dev/null; git bundle create "$OUT/repo.bundle" --all refs/notes/ai 2>/dev/null || true
echo "fixtures in $OUT"; ls "$OUT"
