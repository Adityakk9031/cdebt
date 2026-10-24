"""`cdebt init`: what it would change, shown before anything is changed. Each plan is applied only
after the developer's explicit consent (the CLI asks). Files are only ever created, never
overwritten; the GitHub rule uses the rulesets API, which adds a rule instead of replacing the
branch-protection settings."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cdebt import gitutil

POLICY = """# cdebt policy (read by `cdebt quiz` and the pre-push hook). Trust and agent choice are NEVER
# read from this file; they live in your user config.
questions_per_unit = 3    # asked per function (3 to 5), easy to hard. 2 of 3 right = understood;
                          # one wrong is fine, two wrong is NOT understood (60%, rounded up)
max_questions_per_run = 6 # all functions together in one quiz or push (0 = no cap). Largest
                          # function first; any it does not reach stay NOT understood and are listed
pass_ratio = 0.6
require_hard = true       # ...and at least ONE hard question must be right (when the set has a hard
                          # one), so easy questions alone cannot clear a function
timer = true              # visible countdown on the follow-ups; false asks them untimed (weaker check)
hard_vs_easy_ratio = 1.3  # follow-ups when hard answers took under 1.3 x the easy+medium median time
follow_up_fraction = 0.75 # countdown = 75% of the person's OWN median answer time...
follow_up_min_seconds = 5  # ...never under 5 s
follow_up_max_seconds = 15 # ...never over 15 s
max_follow_ups = 2        # 1 to 2 random short-answer follow-ups (changed input, branch, variable)
enforce = false           # advisory by default; the Action fails the build only when true
"""

WORKFLOW = """name: cdebt
# Advisory by default. Needs Docker (preinstalled on ubuntu runners) because PR code is untrusted.
on:
  pull_request:
    types: [opened, synchronize, reopened]
  issue_comment:
    types: [created]
permissions: {contents: read, pull-requests: write, issues: write, checks: write}
jobs:
  run:
    if: >-
      github.event_name == 'pull_request' ||
      (github.event.issue.pull_request && contains(github.event.comment.body, '/cdebt recheck') &&
       contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          ref: ${{ github.event_name == 'issue_comment' && format('refs/pull/{0}/head', github.event.issue.number) || '' }}
      - run: docker pull python:3.11-slim
      - uses: Adityakk9031/cdebt@v1   # pin a release tag once one exists
        with: {enforce: "false"}
"""


@dataclass
class FilePlan:
    path: Path
    action: str  # create | exists
    message: str
    content: str = ""

    def apply(self) -> None:
        if self.action == "create":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self.content)


def policy_plan(repo: Path) -> FilePlan:
    p = repo / ".cdebt" / "policy.toml"
    if p.exists():
        return FilePlan(p, "exists", f"{p} already exists (left as it is)")
    return FilePlan(p, "create", f"create {p}", POLICY)


def workflow_plan(repo: Path) -> FilePlan:
    p = repo / ".github" / "workflows" / "cdebt.yml"
    if p.exists():
        return FilePlan(p, "exists", f"{p} already exists (left as it is)")
    return FilePlan(p, "create", f"create {p}", WORKFLOW)


def _git(repo: Path, *args: str) -> str | None:
    try:
        return gitutil.run(list(args), repo).strip() or None
    except gitutil.GitError:
        return None


def default_branch(repo: Path) -> str | None:
    """The repository's default branch name, from origin/HEAD, else a local main or master."""
    ref = _git(repo, "rev-parse", "--abbrev-ref", "origin/HEAD")
    if ref and "/" in ref and ref != "origin/HEAD":
        return ref.split("/", 1)[1]
    for name in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{name}"):
            return name
    return None


def workflow_branch_note(repo: Path) -> str:
    """GitHub runs comment-triggered workflows (`/cdebt recheck`) only from the workflow file on
    the DEFAULT branch, so the file must be committed there, not only on a feature branch."""
    rel = ".github/workflows/cdebt.yml"
    cur = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    cur = None if cur == "HEAD" else cur  # detached
    main = default_branch(repo) or "main"
    if cur and cur != main:
        return (
            f"you are on `{cur}`. Commit {rel} to `{main}` (the default branch) and push it: "
            "GitHub runs `/cdebt recheck` only from the default branch's workflow. For example:\n"
            f"  git switch {main} && git checkout {cur} -- {rel} && "
            f"git commit -m 'Add the cdebt workflow' && git push && git switch {cur}"
        )
    return (
        f"commit {rel} on `{main}` and push it (`/cdebt recheck` needs it on the default branch)."
    )


def check_git_ai() -> str:
    exe = shutil.which("git-ai") or str(Path.home() / ".git-ai" / "bin" / "git-ai")
    if not Path(exe).exists():
        return (
            "not installed. cdebt will fall back to commit trailers (coarse) and report the rest "
            "as unknown. Install Git AI per its docs for line-level attribution."
        )
    p = subprocess.run([exe, "--version"], capture_output=True, text=True)
    ver = p.stdout.strip() or "unknown version"
    return (
        f"found {exe} ({ver}). Notes are only written while its daemon/hooks are active; run "
        "`git-ai debug` if `git notes --ref=ai list` is empty."
    )


@dataclass
class RulesetPlan:
    endpoint: str
    body: dict[str, object]
    message: str


def _gh() -> str:
    return os.environ.get("CDEBT_GH") or shutil.which("gh") or "gh"


def ruleset_plan(repo: Path) -> RulesetPlan:
    remote = gitutil.run(["config", "--get", "remote.origin.url"], repo, check=False).strip()
    slug = (
        remote.removesuffix(".git")
        .replace("git@github.com:", "")
        .replace("https://github.com/", "")
    )
    body: dict[str, object] = {
        "name": "cdebt gate",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [{"context": "cdebt / gate"}],
                },
            }
        ],
    }
    msg = (
        f"  POST repos/{slug}/rulesets  (adds a NEW ruleset; existing branch protection and "
        "rulesets are not modified)\n"
        "  effect: the check `cdebt / gate` must succeed before a PR can merge into the default "
        "branch. In advisory mode that check reports `neutral`, which does not block.\n"
        "  note: rulesets on private repositories need a paid GitHub plan; the call will fail "
        "with a clear error otherwise.\n" + json.dumps(body, indent=2)
    )
    return RulesetPlan(f"repos/{slug}/rulesets", body, msg)


def apply_ruleset(plan: RulesetPlan) -> tuple[bool, str]:
    p = subprocess.run(
        [_gh(), "api", "-X", "POST", plan.endpoint, "--input", "-"],
        input=json.dumps(plan.body),
        capture_output=True,
        text=True,
    )
    return p.returncode == 0, (p.stdout if p.returncode == 0 else p.stderr).strip()[-400:]
