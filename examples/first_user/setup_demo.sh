#!/usr/bin/env bash
# Builds a throwaway "first user" demo: a tiny billing project with human-written code on `main`
# (pushed to a local bare "remote") and an AI-written change on a `feature` branch that has NOT
# been pushed yet. The AI change is recorded with Git AI (real line-level notes) when git-ai is
# installed, otherwise with a Co-Authored-By trailer (coarse; cdebt labels it so).
#
# The "AI" here is a stand-in for an agent session (`git-ai checkpoint mock_ai`), so the demo is
# repeatable. Nothing outside the two folders below is touched; no global git config is changed.
#
#   bash examples/first_user/setup_demo.sh            # DEMO_DIR defaults to ~/cdebt-demo
#   DEMO_DIR=/tmp/demo bash examples/first_user/setup_demo.sh
set -euo pipefail

DEMO="${DEMO_DIR:-$HOME/cdebt-demo}"
REMOTE="${DEMO}-remote.git"
if [ -e "$DEMO" ] || [ -e "$REMOTE" ]; then
  echo "already exists: $DEMO or $REMOTE"
  echo "to start over:  rm -rf '$DEMO' '$REMOTE'   (then run this script again)"
  exit 1
fi

GITAI="$HOME/.git-ai/bin/git-ai"
HAVE_GITAI=0
if [ -x "$GITAI" ] || command -v git-ai >/dev/null 2>&1; then
  HAVE_GITAI=1
  export PATH="$HOME/.git-ai/bin:$PATH"
  export GIT_TRACE2_EVENT="af_unix:stream:$HOME/.git-ai/internal/daemon/trace2.sock"
  export GIT_TRACE2_EVENT_NESTING=0
fi

mkdir -p "$DEMO/pkg"
cd "$DEMO"
git init -q -b main
git config user.name "Demo Dev"
git config user.email "demo@example.com"

# --- main: human-written code -------------------------------------------------------------
cat > pkg/__init__.py <<'EOF'
EOF
cat > pkg/config.py <<'EOF'
TAX_PERCENT = 8
FREE_SHIPPING_OVER = 50
EOF
cat > pkg/pricing.py <<'EOF'
def subtotal(prices: list[int]) -> int:
    return sum(prices)
EOF
git add .
[ "$HAVE_GITAI" = 1 ] && git-ai checkpoint human pkg/config.py pkg/pricing.py >/dev/null 2>&1 || true
git commit -qm "Initial project (human-written)"

git init -q --bare -b main "$REMOTE"
git remote add origin "$REMOTE"
git push -q -u origin main

# --- feature: an AI-written change, not pushed yet ----------------------------------------
git checkout -q -b feature
cat >> pkg/pricing.py <<'EOF'


def apply_discount(total: int, loyalty_years: int) -> int:
    if loyalty_years >= 5:
        rate = 20
    elif loyalty_years >= 2:
        rate = 10
    else:
        rate = 0
    return total - total * rate // 100


def late_fee(days_late: int, balance: int) -> int:
    if days_late <= 0:
        return 0
    fee = balance * 2 // 100
    if days_late > 30:
        fee = fee * 2
    return fee


def invoice_total(prices: list[int], years: int, days_late: int) -> int:
    discounted = apply_discount(subtotal(prices), years)
    return discounted + late_fee(days_late, discounted)
EOF
git add pkg/pricing.py
if [ "$HAVE_GITAI" = 1 ]; then
  git-ai checkpoint mock_ai pkg/pricing.py >/dev/null 2>&1
  git commit -qm "Add discounts and late fees (AI-written)"
  git-ai await >/dev/null 2>&1 || true
  SRC="Git AI (line-level)"
else
  git commit -qm "Add discounts and late fees (AI-written)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
  SRC="commit trailer (coarse; install Git AI for line-level attribution)"
fi

if [ "${DEMO_FORCE_FOLLOWUP:-0}" = 1 ]; then
  mkdir -p .cdebt
  printf 'hard_vs_easy_ratio = 1000.0\nmin_median_seconds = 0.0\n' > .cdebt/policy.toml
  git add .cdebt/policy.toml && git commit -qm "Demo policy: always show the timed follow-up (staging, normally rare)"
  echo "note: DEMO_FORCE_FOLLOWUP=1 stages the timed follow-up; say so on camera"
fi

echo
echo "Demo ready:  $DEMO   (remote: $REMOTE)"
echo "Branch:      feature (AI change committed, NOT pushed)"
echo "Attribution: $SRC"
echo "Next:        cd '$DEMO' and follow examples/first_user/DEMO_SCRIPT.md"
