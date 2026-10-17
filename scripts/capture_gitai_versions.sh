#!/usr/bin/env bash
# Captures REAL notes from several historical git-ai releases into tests/fixtures/gitai_versions/.
# Binaries are downloaded into fresh temp dirs, checksum-verified against the release's
# SHA256SUMS, and never installed or put on the global PATH.
set -uo pipefail
OUT="$(cd "$(dirname "$0")/.." && pwd)/tests/fixtures/gitai_versions"
case "$(uname -s)-$(uname -m)" in Darwin-arm64) ASSET=git-ai-macos-arm64;; Darwin-x86_64) ASSET=git-ai-macos-x64;; Linux-x86_64) ASSET=git-ai-linux-x64;; *) echo unsupported; exit 1;; esac
scenario() {  # $1 = tag dir, $2 = use_trace2 (0/1); run inside a subshell
  local W="$1" T2="$2"; local BIN="$W/bin"
  export PATH="$BIN:$PATH" HOME="$W/home"; mkdir -p "$HOME"
  if [ "$T2" = 1 ]; then export GIT_TRACE2_EVENT="af_unix:stream:$HOME/.git-ai/internal/daemon/trace2.sock" GIT_TRACE2_EVENT_NESTING=0; fi
  local R="$W/repo$T2"; mkdir -p "$R"; cd "$R"
  git init -q -b main; git config user.email dev@example.com; git config user.name Dev
  printf 'def human(x):\n    return x + 1\n' > a.py
  git add .; git-ai checkpoint human a.py >/dev/null 2>&1; git commit -qm "c1 human"
  printf 'def ai_fn(x):\n    return x * 2\n\ndef ai_two(y):\n    return y - 1\n' >> a.py
  git-ai checkpoint mock_ai a.py >/dev/null 2>&1 || git-ai checkpoint mock_ai >/dev/null 2>&1
  git commit -qam "c2 ai"; git-ai await >/dev/null 2>&1; sleep 1
  REPO="$R"
}
for TAG in "$@"; do
  W="$(mktemp -d /private/tmp/ga.XXXXXX)"; BIN="$W/bin"; mkdir -p "$BIN"
  curl -sSL -o "$BIN/git-ai" "https://github.com/git-ai-project/git-ai/releases/download/$TAG/$ASSET" || { echo "$TAG: download failed"; continue; }
  curl -sSL -o "$W/SHA256SUMS" "https://github.com/git-ai-project/git-ai/releases/download/$TAG/SHA256SUMS"
  want=$(grep " $ASSET\$" "$W/SHA256SUMS" | awk '{print $1}'); got=$(shasum -a 256 "$BIN/git-ai" | awk '{print $1}')
  [ -n "$want" ] && [ "$want" = "$got" ] || { echo "$TAG: checksum mismatch/missing, skipped"; continue; }
  chmod +x "$BIN/git-ai"; ln -sf git-ai "$BIN/git"
  D="$OUT/$TAG"; rm -rf "$D"; mkdir -p "$D"; mode=none
  for T2 in 0 1; do
    ( scenario "$W" $T2; echo "$REPO" > "$W/repo_path" ) >/dev/null 2>&1
    R=$(cat "$W/repo_path" 2>/dev/null) || continue
    if (cd "$R" && PATH="$BIN:$PATH" HOME="$W/home" git notes --ref=ai list 2>/dev/null | grep -q .); then mode=$([ $T2 = 1 ] && echo trace2 || echo wrapper); break; fi
  done
  R=$(cat "$W/repo_path" 2>/dev/null || true)
  [ -n "$R" ] && cd "$R" || { echo "$TAG: no repo"; continue; }
  export PATH="$BIN:$PATH" HOME="$W/home"
  v=$(git-ai --version 2>&1 | head -1); echo "$v" > "$D/version.txt"; echo "$mode" > "$D/mode.txt"
  n=0
  for c in $(git rev-list --reverse HEAD); do
    name="note_$(git log -1 --format=%s "$c" | cut -d' ' -f1).txt"
    if git notes --ref=ai show "$c" > "$D/$name" 2>/dev/null; then n=$((n+1)); else rm -f "$D/$name"; fi
  done
  git blame --porcelain a.py > "$D/blame_a.porcelain"; git log --format='%H %s' > "$D/log.txt"; cp a.py "$D/final_a.py"
  git bundle create "$D/repo.bundle" --all refs/notes/ai >/dev/null 2>&1 || git bundle create "$D/repo.bundle" --all >/dev/null 2>&1
  echo "$TAG ($v) mode=$mode: $n note(s)"
  cd /; rm -rf "$W"
done
