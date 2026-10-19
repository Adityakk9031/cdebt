"""PIPELINE TEST of `cdebt study` with SYNTHETIC, clearly fake participants. Not evidence of
anything: the 'participants' are a random number generator whose accuracy we chose.
Writes docs/dry_run/STUDY_DRY_RUN.md from the real command output."""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from cdebt import study as S  # noqa: E402

env = {**os.environ, "CDEBT_TRUSTED": "1", "CDEBT_NO_CACHE": "1"}
CD = ["uv", "run", "--project", str(ROOT), "cdebt"]


def sh(args: list[str], cwd: Path | None = None) -> str:
    p = subprocess.run(args, capture_output=True, text=True, cwd=cwd, env=env)
    return (p.stdout + p.stderr).strip()


def git(d: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=d, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@x.invalid",
                        "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@x.invalid"})


work = Path(tempfile.mkdtemp(prefix="cdebt-dryrun-"))
repo = work / "repo"
repo.mkdir()
git(repo, "init", "-q", "-b", "main")
for i in range(5):
    body = "".join(f"def f{i}_{k}(a: int, b: int) -> int:\n    return a * {k + 2} + b - {i}\n\n\n" for k in range(4))
    (repo / f"mod{i}.py").write_text(f"LIMITS{i} = [{i}, {i + 1}, {i + 2}]\nTOTAL{i} = sum(LIMITS{i}) * {i + 3}\n\n\n{body}")
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "synthetic repo\n\nCo-Authored-By: Claude <noreply@anthropic.com>")

report: list[str] = [
    "# `cdebt study` dry run (PIPELINE TEST, SYNTHETIC DATA)", "",
    "**Everything below is fake.** The repository is generated, the 'participants' are a random "
    "number generator with accuracy rates chosen by the script, and the numbers prove nothing about "
    "people or about cdebt. This only shows that `init`, `run`, `analyze` and `power` work end to end "
    "and that the privacy properties hold. Regenerate with `uv run python scripts/study_dry_run.py`.", ""]
sf, kf, rdir = work / "study.json", work / "study.secret.json", work / "responses"
out = sh(CD + ["study", "init", "--repo", str(repo), "--out", str(sf), "--secret", str(kf), "--n", "10",
               "--kinds", "runtime,constant", "--top-files", "5", "--seed", "7"])
report += ["## 1. `study init`", "", "```", re.sub(str(work), "<tmp>", out), "```", ""]
study = S.StudyFile.model_validate_json(sf.read_text())
sec = S.SecretFile.model_validate_json(kf.read_text())

rng = random.Random(20261019)


def correct_letter(p: S.StudyProbe) -> str:
    """The simulator's oracle: find the right option using the coordinator secret."""
    from itertools import combinations
    letters = "ABCDEFGH"
    if not p.multi:
        return next(letters[i] for i in range(len(p.options)) if S.grade(study, sec, p.id, letters[i]))
    for r in range(1, len(p.options) + 1):
        for c in combinations(range(len(p.options)), r):
            a = ",".join(letters[i] for i in c)
            if S.grade(study, sec, p.id, a):
                return a
    return "A"


def wrong_answer(p: S.StudyProbe, good: str) -> str:
    for i in "ABCD":
        if i != good and i in "ABCD"[: len(p.options)]:
            return i
    return "A"


def fake_participant(team: str, p_fam: float, p_never: float, k: int) -> None:
    ans = {}
    for p in study.probes:
        fam = rng.choice(["wrote", "reviewed", "never"])
        good = correct_letter(p)
        ok = rng.random() < (p_never if fam == "never" else p_fam)
        ans[p.id] = {"answer": good if ok else wrong_answer(p, good), "familiarity": fam,
                     "consulted": rng.random() < 0.1}
    f = work / f"fake-answers-{k}.json"
    f.write_text(json.dumps(ans))
    sh(CD + ["study", "run", str(sf), "--out-dir", str(rdir), "--team", team, "--answers", str(f)])


# phase A: 3 fake participants only -> below the minimum, must be suppressed / refused
for k in range(3):
    fake_participant("FAKE-team-A", 0.85, 0.45, k)
a3 = sh(CD + ["study", "analyze", str(sf), str(rdir), "--secret", str(kf), "--reps", "200"])
report += ["## 2. Only 3 fake participants (below the minimum of 5)", "", "```", a3, "```", "",
           "Every cell is suppressed and no contrast is given.", ""]
# phase B: 9 more (team A = 8 total, team B = 4 total)
for k in range(3, 8):
    fake_participant("FAKE-team-A", 0.85, 0.45, k)
for k in range(8, 12):
    fake_participant("FAKE-team-B", 0.85, 0.45, k)
a12 = sh(CD + ["study", "analyze", str(sf), str(rdir), "--secret", str(kf), "--reps", "500", "--json"])
res = json.loads(a12)
report += ["## 3. 12 fake participants (8 in team A, 4 in team B)", "",
           "Planted by the script: accuracy 0.85 if familiar, 0.45 if never saw. The point is only that "
           "the pipeline recovers a planted difference; it says nothing real.", "", "```json",
           json.dumps({k: res[k] for k in ("participants", "teams", "responses", "by_familiarity",
                                           "familiar_vs_never", "by_team", "notes")}, indent=1), "```", "",
           "Team B has 4 participants (< 5), so its cell is suppressed.", ""]
report += ["## 4. `study power`", "", "```", sh(CD + ["study", "power"]), "```", ""]

# ---- privacy / leak checks, each asserted ------------------------------------------------------
checks: list[tuple[str, bool]] = []
study_text = sf.read_text()
checks.append(("study file contains no HMAC key and no answer MACs",
               sec.key not in study_text and not any(m in study_text for m in sec.answer_macs.values())))
checks.append(("study file has no 'answer' field per question", '"answer"' not in study_text))
checks.append(("secret file is a separate file", kf != sf and "answer_macs" in kf.read_text()))
files = sorted(rdir.glob("response-*.json"))
checks.append(("12 response files written", len(files) == 12))
ids = [json.loads(f.read_text())["participant"] for f in files]
checks.append(("participant ids are random 8-hex", all(re.fullmatch(r"[0-9a-f]{8}", i) for i in ids)))
checks.append(("response files hold no name/host/time fields",
               all(set(json.loads(f.read_text())) == {"schema", "study_id", "participant", "team", "date", "answers"}
                   for f in files)))
analysis_text = a3 + a12
checks.append(("no participant id appears in any analysis output", not any(i in analysis_text for i in ids)))
checks.append(("3-participant analysis suppressed every cell",
               "suppressed" in a3 and "not interpretable" in " ".join(a3.split())))
checks.append(("team B (4 participants) is suppressed", res["by_team"]["FAKE-team-B"]["suppressed"] is True))
checks.append(("team A (8 participants) is shown", res["by_team"]["FAKE-team-A"]["suppressed"] is False))
checks.append(("the planted effect is recovered by the pipeline (synthetic)",
               res["familiar_vs_never"]["difference"] is not None and res["familiar_vs_never"]["difference"] > 0.1))
report += ["## 5. Privacy and leak checks (asserted by the script)", "", "| check | result |", "|---|---|"]
for name, ok in checks:
    report.append(f"| {name} | {'PASS' if ok else 'FAIL'} |")
report += ["", "These are pipeline checks. Do not cite any number on this page as evidence.", ""]
(ROOT / "docs" / "dry_run" / "STUDY_DRY_RUN.md").write_text("\n".join(report))
bad = [n for n, ok in checks if not ok]
print(f"{len(checks) - len(bad)}/{len(checks)} checks passed", bad)
sys.exit(1 if bad else 0)
