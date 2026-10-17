"""Pilot round 2 (needs scripts/pilot.sh outputs): `cdebt coverage` per repo, then a SIMULATED
attestation of the top-25 files in a throwaway copy of each clone, then the gate again.
Usage: uv run python scripts/pilot_attest.py WORKDIR docs/case_studies"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

work, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
proj = Path(__file__).resolve().parent.parent
CD = ["uv", "run", "--project", str(proj), "cdebt"]
env_nodocker = {"CDEBT_NO_DOCKER": "1"}
import os  # noqa: E402

env = {**os.environ, **env_nodocker}


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(CD + args, capture_output=True, text=True, cwd=cwd, env=env)


summary: dict[str, dict] = {}
for name in ("space_chassis", "hive", "agent-meta"):
    repo = work / name
    r = run(["coverage", "--repo", str(repo), "--top-n", "25", "--json"])
    cov = json.loads(r.stdout)
    (out / f"{name}.coverage.json").write_text(json.dumps(cov, indent=1))
    top = [f["path"] for f in cov["files"]]
    copy = work.parent / "cdebt-pilot-attested" / name
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(repo, copy, symlinks=True)
    shutil.rmtree(copy / ".cdebt", ignore_errors=True)
    if (repo / ".cdebt" / "cache").exists():  # keep the SCIP index (cache only)
        shutil.copytree(repo / ".cdebt" / "cache", copy / ".cdebt" / "cache")
    refused = attested = 0
    for rel in top:
        if not rel.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".mjs")):
            continue
        a = run(["attest", rel, "--repo", str(copy), "--note", "pilot simulation: not a real review"],
                cwd=copy)
        refused += a.stdout.count("REFUSED")
        attested += a.stdout.count("attested (")
    gate = {}
    for pol, extra in (("default", []), ("verified", ["--require", "verified"]),
                       ("attested", ["--require", "attested"])):
        g = run(["gate", "--repo", str(copy), "--base", "HEAD~25", "--json", *extra], cwd=copy)
        d = json.loads(g.stdout)
        gate[pol] = {"passed": d["passed"], "ai_lines_unverified": d["ai_lines_unverified"],
                     "violating_units": len(d["violations"]),
                     "attested_accepted_lines": d["attested_accepted_lines"]}
    before = json.loads((out / f"{name}.gate.json").read_text())
    summary[name] = {"top_files": len(top), "units_attested": attested,
                     "attest_refused_quizzable": refused,
                     "gate_before": {"ai_lines_unverified": before["ai_lines_unverified"],
                                     "violating_units": before["violations_total"]},
                     "gate_after_attesting_top25": gate}
    print(name, json.dumps(summary[name]))
(out / "attest_simulation.json").write_text(json.dumps(summary, indent=1))
