#!/usr/bin/env bash
# 60-second demo on a throwaway repo. Uses commit trailers for attribution so it needs no
# extra tools. (With Git AI installed you get line-level attribution instead.)
set -euo pipefail
D="$(mktemp -d)"; cd "$D"
git init -q -b main; git config user.email dev@example.com; git config user.name Dev
mkdir pkg; touch pkg/__init__.py
printf 'def total(prices: list[int], tax: int) -> int:\n    return sum(prices) + tax\n\n\ndef label(name: str, n: int) -> str:\n    return name.upper() * n\n' > pkg/shop.py
git add -A; git commit -qm "feat: shop helpers

Co-Authored-By: Claude <noreply@anthropic.com>"
git branch base
echo "== 1. map: where is un-understood AI code?"
cdebt map --no-scip
echo; echo "== 2. quiz (scripted answerer; run interactively without --answers)"
cdebt quiz pkg/shop.py --kinds runtime --n 4 --emit q.json
python - <<'PY'
import json, pathlib
from cdebt.config import Config
from cdebt.quiz import build_quiz
probes, _, _ = build_quiz(pathlib.Path('.'), 'pkg/shop.py', Config(), n=4, kinds=('runtime',))
pathlib.Path('a.json').write_text(json.dumps({p.id: p.answer for p in probes}))
PY
cdebt quiz pkg/shop.py --kinds runtime --n 4 --answers a.json
echo; echo "== 3. map again: understood share rises"
cdebt map --no-scip
echo; echo "== 4. edit the code: understanding expires"
sed -i.bak 's/+ tax/+ tax + 0/' pkg/shop.py; rm pkg/shop.py.bak
git commit -qam "tweak total

Co-Authored-By: Claude <noreply@anthropic.com>"
cdebt gate --base base   # advisory by default: reports, exits 0
cdebt gate --base base --enforce || echo "(gate exit code $? with --enforce: expected 1, total() changed and its understanding expired)"
