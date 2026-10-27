"""Benchmark: does batching + retry raise how many functions get >= 3 verified agent questions?
Usage (run with a NON-editable install of cdebt in the Python that runs this):
  python scripts/bench_agent_reach.py <repo> <out.json> before|after <file.py> [<file.py> ...]
`before` = one agent run per file, one round (the old behaviour); `after` = the defaults (batches
of 6, up to 2 rounds). Needs the Claude Code CLI logged in (it uses some of your usage). The repo
must be a git repo (the sandbox copies tracked files) and its dependencies must be importable by
this Python; an editable install of the project under test makes every import fail the sandbox's
read check."""

import json
import sys
import time
from collections import Counter
from pathlib import Path

from cdebt import sandbox
from cdebt.agents import propose_with_agent
from cdebt.agents.session import targets_for_file
from cdebt.agents.userconfig import AgentChoice
from cdebt.config import load_config

sandbox.set_default_isolation("strong")
repo = Path(sys.argv[1])
out = Path(sys.argv[2])
mode = sys.argv[3]
files = sys.argv[4:]
cfg = load_config(repo)
choice = AgentChoice(name="claude_code")
kw = dict(batch=999, rounds=1, timeout=170) if mode == "before" else {}
rows = []
for rel in files:
    n_t = len(targets_for_file(repo, rel, cfg))
    t0 = time.monotonic()
    probes, notes = propose_with_agent(repo, rel, cfg, choice, per_target=6, **kw)
    c = Counter(p.symbol for p in probes)
    row = dict(
        mode=mode,
        file=rel,
        targets=n_t,
        with1=sum(1 for v in c.values() if v >= 1),
        with3=sum(1 for v in c.values() if v >= 3),
        questions=len(probes),
        seconds=round(time.monotonic() - t0),
        notes=[x[:140] for x in notes][-4:],
    )
    rows.append(row)
    print(json.dumps(row), flush=True)
    out.write_text(json.dumps(rows, indent=1))
print("DONE", flush=True)
