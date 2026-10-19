"""Smoke test of a built wheel in a CLEAN venv, on any OS: install, run read-only commands on a
throwaway git repo, and check that code-executing commands work (POSIX) or refuse clearly (Windows).
Usage: python scripts/smoke_install.py dist/cdebt-*.whl"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

import glob

_arg = sys.argv[1]
wheel = Path(sorted(glob.glob(_arg))[0] if "*" in _arg else _arg).resolve()
tmp = Path(tempfile.mkdtemp(prefix="cdebt-smoke-"))
env_dir = tmp / "venv"
venv.EnvBuilder(with_pip=True).create(env_dir)
bindir = env_dir / ("Scripts" if os.name == "nt" else "bin")
py = str(bindir / ("python.exe" if os.name == "nt" else "python"))
cd = str(bindir / ("cdebt.exe" if os.name == "nt" else "cdebt"))


def run(args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = {**os.environ, **(env or {})}
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd, env=e)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail[:160]}]" if detail and not ok else ""))
    if not ok:
        sys.exit(1)


r = run([py, "-m", "pip", "install", "--quiet", str(wheel)])
check("pip install wheel into a clean venv", r.returncode == 0, r.stderr)
r = run([py, "-m", "pip", "list", "--format=json"])
pkgs = {p["name"].lower() for p in json.loads(r.stdout)}
check("core install has no heavy backtest dependencies", not ({"numpy", "pyarrow"} & pkgs), str(pkgs))
r = run([cd, "--help"])
check("cdebt --help lists the commands", all(c in r.stdout for c in ("map", "gate", "quiz", "coverage", "study")), r.stdout + r.stderr)
r = run([py, "-c", "import cdebt, cdebt.study, cdebt.cache, cdebt.sandbox; print(cdebt.__file__)"])
check("package imports (incl. sandbox) on this OS", r.returncode == 0, r.stderr)

repo = tmp / "repo"
repo.mkdir()
gitenv = {"GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@x.invalid", "GIT_COMMITTER_NAME": "x",
          "GIT_COMMITTER_EMAIL": "x@x.invalid", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf",
          "GIT_CONFIG_VALUE_0": "false"}
run(["git", "init", "-q", "-b", "main"], repo)
(repo / "m.py").write_text("LIMIT = 3\n\n\ndef f(a: int) -> int:\n    return a + LIMIT\n")
run(["git", "add", "-A"], repo, gitenv)
run(["git", "commit", "-q", "-m", "ai\n\nCo-Authored-By: Claude <noreply@anthropic.com>"], repo, gitenv)
r = run([cd, "map", "--repo", str(repo), "--no-scip", "--json"])
check("cdebt map --json works", r.returncode == 0 and json.loads(r.stdout)["summary"]["ai_lines"] == 5, r.stdout + r.stderr)
r = run([cd, "plan", "--repo", str(repo), "--no-scip", "--json"])
check("cdebt plan --json works", r.returncode == 0, r.stdout + r.stderr)
r = run([cd, "gate", "--repo", str(repo), "--base", "HEAD", "--json"])
check("cdebt gate runs (advisory, exit status success)", r.returncode == 0 and json.loads(r.stdout)["enforced"] is False, r.stdout + r.stderr)
r = run([cd, "migrate", "--repo", str(repo)])
check("cdebt migrate runs", r.returncode == 0, r.stdout + r.stderr)
if os.name == "nt":
    r = run([cd, "quiz", "m.py", "--repo", str(repo)], repo)
    check("quiz refuses on Windows with the WSL2 message", r.returncode == 2 and "WSL2" in " ".join((r.stdout + r.stderr).split()), r.stdout + r.stderr)
else:
    r = run([cd, "quiz", "m.py", "--repo", str(repo), "--kinds", "runtime,constant", "--emit", str(tmp / "q.json"), "--trusted"], repo)
    check("quiz --emit works with --trusted (probes executed in the sandbox)", r.returncode == 0 and (tmp / "q.json").exists(), r.stdout + r.stderr)
    qs = json.loads((tmp / "q.json").read_text())
    check("emitted questions contain no answers", qs and all("answer" not in q for q in qs))
print(f"smoke test OK on {sys.platform} (python {sys.version_info.major}.{sys.version_info.minor})")
