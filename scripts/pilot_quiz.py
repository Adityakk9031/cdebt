"""Second half of the pilot (run after scripts/pilot.sh): probe coverage over the 25 most-flagged
Python files per repo, and one scripted quiz session on agent-meta (all answers correct except
the first multi-select one, which is deliberately wrong to show the failure path)."""

from __future__ import annotations

import collections
import json
import subprocess
import sys
import time
from pathlib import Path

from cdebt.config import Config
from cdebt.quiz import build_quiz

work, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
cov: dict[str, dict] = {}
for name, kinds in (
    ("space_chassis", ("runtime",)),
    ("agent-meta", ("runtime",)),
    ("agent-meta+scip", ("runtime", "callgraph")),
):
    repo = work / name.split("+")[0]
    m = json.loads((out / f"{name.split('+')[0]}.map.json").read_text())
    files = [f["path"] for f in m["files"] if f["path"].endswith(".py")][:25]
    kinds_c, with_p, t0, reasons = collections.Counter(), 0, time.time(), collections.Counter()
    for rel in files:
        ps, _, sk = build_quiz(repo, rel, Config(), n=6, seed=0, kinds=kinds)
        with_p += bool(ps)
        kinds_c.update(p.kind for p in ps)
        for s in sk:
            r = s.split(": ", 2)[-1]
            reasons[r.split("(")[0].strip()[:50]] += 1
    cov[name] = {
        "files_checked": len(files),
        "files_with_probes": with_p,
        "probes_by_kind": dict(kinds_c),
        "seconds": round(time.time() - t0),
        "top_skip_reasons": reasons.most_common(6),
    }
(out / "probe_coverage.json").write_text(json.dumps(cov, indent=2))

repo = work / "agent-meta"
m = json.loads((out / "agent-meta.map-scip.json").read_text())
pick = None
for f in m["files"]:
    if not f["path"].endswith(".py"):
        continue
    ps, _, _ = build_quiz(repo, f["path"], Config(), n=6, seed=0, kinds=("runtime", "callgraph"))
    if {p.kind for p in ps} == {"runtime", "callgraph"}:
        pick = (f["path"], ps)
        break
assert pick, "no file with both probe kinds"
rel, ps = pick
ans: dict[str, str] = {}
wrong_done = False
for p in ps:
    if not p.multi:
        ans[p.id] = p.answer
    elif not wrong_done:
        ans[p.id] = "A"  # deliberately wrong unless A happens to be right
        wrong_done = True
    else:
        ans[p.id] = ",".join(
            "ABCDEFGH"[i] for i, o in enumerate(p.options) if o in json.loads(p.answer)
        )
(out / "quiz-answers.json").write_text(json.dumps(ans))
cmd = [
    "uv",
    "run",
    "--project",
    str(Path(__file__).resolve().parent.parent),
    "cdebt",
    "quiz",
    rel,
    "--repo",
    str(repo),
    "--n",
    "6",
    "--seed",
    "0",
    "--kinds",
    "runtime,callgraph",
    "--answers",
    str(out / "quiz-answers.json"),
    "--label",
    "pilot-audit",
    "--verbose",
]
r = subprocess.run(cmd, capture_output=True, text=True, cwd=repo)
(out / "quiz-session.txt").write_text(
    f"$ cdebt quiz {rel} --n 6 --seed 0 --kinds runtime,callgraph "
    f"--answers <scripted> --label pilot-audit\n{r.stdout}{r.stderr}"
)
print(rel)
