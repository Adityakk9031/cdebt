"""Benchmark: does the developer's agent raise how much AI-written code a quiz can verify?
Per file: units with >=1 verifiable question from (a) the builtin generator, (b) the agent, (c) both.
Also: every agent return_value probe is re-checked through a SECOND sandbox path (the plain 'call'
runner mode) and compared. Output: results.json in the clones folder (+ a log line per file).
Usage: python scripts/bench_agent_coverage.py <clones-folder> [files-per-repo]
Needs the Claude Code CLI logged in (it uses a little of your usage) and the clones checked out at
the commits in docs/CASE_STUDIES.md (space_chassis 9a0430a37c, agent-meta 8457634f1e)."""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

from cdebt import sandbox
from cdebt.agents import _run_agent, adapters
from cdebt.agents.probes import Rejected, execute, parse_proposal
from cdebt.agents.session import Session, targets_for_file
from cdebt.agents.userconfig import AgentChoice
from cdebt.config import load_config
from cdebt.probes.base import copy_tree, temp_root
from cdebt.probes.runtime import module_info
from cdebt.quiz import unit_probe_kinds
from cdebt.sandbox import run_in_sandbox

BENCH = Path(sys.argv[1]).resolve()  # a folder holding clones of space_chassis and agent-meta
TOP = int(sys.argv[2]) if len(sys.argv) > 2 else 6
sandbox.set_default_isolation("strong")  # the untrusted default (strict Seatbelt here)
choice = AgentChoice(name="claude_code")


def cross_check(repo: Path, rel: str, probe) -> str:  # type: ignore[no-untyped-def]
    """Re-run a plain return_value probe through the 'call' runner mode and compare answers."""
    spec = probe.evidence.get("spec", {})
    ci = spec.get("call_inputs", {})
    if spec.get("harness_code") or ci.get("kwargs") or "." in probe.symbol:
        return "not-comparable"
    mod, roots = module_info(repo, rel)
    root = temp_root("cdebt-xchk-")
    try:
        copy_tree(repo, root)
        res = run_in_sandbox(
            {
                "mode": "call", "module": mod, "func": probe.symbol,
                "args_repr": repr(tuple(ci.get("args", []))),
                "sys_path": [str(root / r) for r in roots],
            },
            root=root, timeout=15,
        )  # fmt: skip
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    pl = res.payload or {}
    if "results" not in pl:
        return "second-path-failed"
    kind, value = pl["results"][0]
    other = value if kind == "ok" else f"raises {value}"
    return "agree" if other == probe.answer else "DISAGREE"


results = []
for name in ("space_chassis", "agent-meta"):
    repo = BENCH / name
    cfg = load_config(repo)
    import subprocess

    m = json.loads(
        subprocess.run(
            ["cdebt", "map", "--repo", str(repo), "--no-scip", "--json"],
            capture_output=True, text=True,
        ).stdout
    )  # fmt: skip
    files = [f for f in m["files"] if f["path"].endswith(".py")][:TOP]
    for f in files:
        rel = f["path"]
        t0 = time.monotonic()
        ai_units: dict[str, int] = f["ai_units"]
        total_ai = sum(ai_units.values())
        avail, _skips = unit_probe_kinds(repo, rel, cfg)
        builtin_units = {s for s in avail if s in ai_units}
        targets = targets_for_file(repo, rel, cfg)
        row = {
            "repo": name, "file": rel, "ai_lines": total_ai, "ai_units": len(ai_units),
            "builtin_units": len(builtin_units),
            "builtin_lines": sum(ai_units[s] for s in builtin_units),
            "targets_offered": len(targets),
        }  # fmt: skip
        agent_units: set[str] = set()
        reasons: Counter[str] = Counter()
        checks: Counter[str] = Counter()
        proposals = accepted = verified = 0
        note = ""
        if targets:
            sess = Session.create(repo, "claude_code", targets, 6)
            try:
                run = adapters.build(choice, sess.dir, repo, 6)
                code, tail = _run_agent(run.argv, run.env, run.cwd, 170)
                recs = sess.read()
                proposals = len(recs)
                if code != 0 and not recs:
                    note = f"agent produced nothing: {tail.strip()[-120:]}"
                by_id = {t.id: t for t in targets}
                for r in recs:
                    if not r["accepted"]:
                        reasons[str(r["why"])[:70]] += 1
                        continue
                    accepted += 1
                    t = by_id[str(r["target_id"])]
                    try:
                        p = execute(
                            repo,
                            t.path,
                            parse_proposal(r["spec"]),
                            cfg,
                            proposer="agent:claude_code",
                        )
                    except Rejected as e:
                        reasons["re-run: " + str(e)[:60]] += 1
                        continue
                    verified += 1
                    agent_units.add(p.symbol)
                    if p.kind == "return_value":
                        checks[cross_check(repo, rel, p)] += 1
            finally:
                sess.close()
        both = builtin_units | agent_units
        row.update(
            proposals=proposals, accepted=accepted, verified=verified,
            agent_units=len([s for s in agent_units if s in ai_units]),
            union_units=len([s for s in both if s in ai_units]),
            union_lines=sum(ai_units.get(s, 0) for s in both),
            rejection_reasons=dict(reasons.most_common(6)), cross_check=dict(checks),
            seconds=round(time.monotonic() - t0), note=note,
        )  # fmt: skip
        results.append(row)
        print(json.dumps(row), flush=True)
        (BENCH / "results.json").write_text(json.dumps(results, indent=1))
print("DONE", flush=True)
