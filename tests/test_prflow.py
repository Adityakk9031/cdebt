"""Milestone 3: notes, hook, init, quiz --pr, CI re-execution and the sticky comment.

Real git (temp repos, a local bare remote), a fake `gh` script, a scripted terminal file. No
network."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cdebt import adaptive, hook, notes
from cdebt.cli import app
from cdebt.config import Config
from cdebt.gate import run_gate, to_markdown
from cdebt.models import Record
from cdebt.probes.base import LETTERS
from conftest import Repo, git

pytestmark = pytest.mark.posix_sandbox

AI = "Co-Authored-By: Claude <noreply@anthropic.com>"
BASE_CODE = "def inc(x: int) -> int:\n    return x + 1\n"
AI_CODE = (
    "def inc(x: int) -> int:\n    return x + 2\n\n\n"
    "def classify(n: int, strict: bool = False) -> str:\n"
    "    if n < 0:\n        label = 'neg'\n    elif n == 0:\n        label = 'zero'\n"
    "    else:\n        label = 'pos'\n    if strict and n > 100:\n        label = label + '!'\n"
    "    return label\n"
)
POLICY = "min_median_seconds = 1000000.0\n"  # deterministic: never trigger timing follow-ups


def _wrapper(tmp: Path) -> Path:
    """A `cdebt` executable for the hook (the package may not be installed on PATH)."""
    w = tmp / "bin" / "cdebt"
    w.parent.mkdir(exist_ok=True)
    w.write_text(f'#!/bin/sh\nexec {sys.executable} -c "from cdebt.cli import app; app()" "$@"\n')
    w.chmod(w.stat().st_mode | stat.S_IXUSR)
    return w


def _setup(make_repo, tmp_path: Path) -> tuple[Repo, Path]:
    r: Repo = make_repo("work")
    git(r.path, "config", "user.email", "d@example.com")
    git(r.path, "config", "user.name", "Dev")
    r.write("m.py", BASE_CODE)
    r.write(".cdebt/policy.toml", POLICY)
    r.commit("base")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    git(r.path, "remote", "add", "origin", str(remote))
    git(r.path, "push", "-q", "origin", "main")
    log = tmp_path / "arrivals.log"
    pre = remote / "hooks" / "pre-receive"
    pre.write_text(f'#!/bin/sh\nwhile read o n ref; do echo "$ref" >> {log}; done\n')
    pre.chmod(pre.stat().st_mode | stat.S_IXUSR)
    git(r.path, "checkout", "-q", "-b", "feature")
    r.write("m.py", AI_CODE)
    r.commit(f"AI change\n\n{AI}")
    return r, remote


def _oracle_answers(repo: Path, tmp_path: Path, wrong_first: bool = False) -> Path:
    """The terminal file for an always-right quiz of `repo`, found by dry-running the same quiz on
    a COPY with an oracle (the copy absorbs the notes it writes)."""
    clone = tmp_path / "oracle"
    shutil.copytree(repo, clone)
    seq: list[str] = []
    orig = adaptive.tty_ask

    def oracle(p, timeout):  # type: ignore[no-untyped-def]
        right = LETTERS[p.options.index(p.answer)]
        wrong = LETTERS[next(i for i, o in enumerate(p.options) if o != p.answer)]
        seq.append(wrong if wrong_first and not seq else right)
        return seq[-1], 0.0

    adaptive.tty_ask = oracle  # type: ignore[assignment]
    try:
        hook.quiz_changes(clone, Config(), "origin/main", "HEAD", lambda m: None)
    finally:
        adaptive.tty_ask = orig  # type: ignore[assignment]
    f = tmp_path / "answers.txt"
    f.write_text("\n".join(seq) + "\n")
    return f


def _env(**extra: str) -> dict[str, str]:
    e = dict(os.environ)
    e.update({"CDEBT_TRUSTED": "1", **extra})
    return e


def _push(r: Repo, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "push", "origin", "feature", *args],
        cwd=r.path, capture_output=True, text=True, env=env,
    )  # fmt: skip


# ---- notes ----


def test_notes_roundtrip_union_and_malformed_notes_are_ignored(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", "x = 1\n")
    sha = r.commit("a")
    rec = Record(unit_path="a.py", unit_symbol="f", content_hash="h1", passed=True)
    notes.write_records(r.path, sha, [rec])
    notes.write_records(r.path, sha, [rec.model_copy(update={"unit_symbol": "g"})])
    got = notes.read_commit(r.path, sha)
    assert sorted(x.unit_symbol for x in got) == ["f", "g"]
    assert "refs/notes/cdebt" in git(r.path, "for-each-ref", "refs/notes/")
    git(r.path, "notes", "--ref=cdebt", "add", "-f", "-m", "{not json", sha)
    assert notes.read_commit(r.path, sha) == []  # garbage can only mean "not understood"


# ---- hook installation ----


def test_hook_install_creates_chains_and_never_overwrites(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", "x = 1\n")
    r.commit("a")
    plan = hook.plan_install(r.path, "/usr/bin/cdebt")
    assert plan.action == "create"
    hook.apply_install(plan)
    assert os.access(plan.path, os.X_OK) and hook.BEGIN in plan.path.read_text()
    assert hook.plan_install(r.path).action == "already"
    # an existing user hook is kept byte for byte; the block is appended after it
    mine = "#!/bin/sh\necho my own check\n"
    plan.path.write_text(mine)
    p2 = hook.plan_install(r.path, "/usr/bin/cdebt")
    assert p2.action == "append"
    hook.apply_install(p2)
    text = plan.path.read_text()
    assert text.startswith(mine) and hook.BEGIN in text
    # a pre-commit-framework hook and a non-script hook are never touched
    plan.path.write_text("#!/usr/bin/env python\n# File generated by pre-commit: do not edit\n")
    assert hook.plan_install(r.path).action == "manual"
    plan.path.write_bytes(b"\x7fELF binary")
    assert hook.plan_install(r.path).action == "manual"


def test_hook_honours_core_hooks_path_and_husky(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", "x = 1\n")
    r.commit("a")
    git(r.path, "config", "core.hooksPath", ".githooks")
    assert hook.plan_install(r.path).path == r.path / ".githooks" / "pre-push"
    git(r.path, "config", "core.hooksPath", ".husky/_")
    assert hook.plan_install(r.path).path == r.path / ".husky" / "pre-push"


# ---- the full local flow: hook -> notes pushed first -> branch ----


def test_push_with_hook_quizzes_writes_notes_and_pushes_them_first(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r, remote = _setup(make_repo, tmp_path)
    answers = _oracle_answers(r.path, tmp_path)
    hook.apply_install(hook.plan_install(r.path, str(_wrapper(tmp_path))))
    before = run_gate(r.path, Config(), "origin/main")
    assert not before.passed and before.ai_lines_unverified > 0

    res = _push(r, _env(CDEBT_TTY=str(answers)))
    assert res.returncode == 0, res.stderr
    assert "UNDERSTOOD" in res.stderr and "notes pushed before the branch" in res.stderr
    arrivals = (tmp_path / "arrivals.log").read_text().split()
    assert arrivals.index("refs/notes/cdebt") < arrivals.index("refs/heads/feature")
    remote_refs = subprocess.run(
        ["git", "ls-remote", str(remote)], capture_output=True, text=True
    ).stdout
    assert "refs/notes/cdebt" in remote_refs and "refs/heads/feature" in remote_refs
    after = run_gate(r.path, Config(), "origin/main")
    assert after.passed and after.understood, after.notes
    md = to_markdown(after, pr=7)
    assert "understood (verified by execution)" in md and "Dev" not in md


def test_no_terminal_prints_a_notice_and_never_blocks_the_push(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r, remote = _setup(make_repo, tmp_path)
    hook.apply_install(hook.plan_install(r.path, str(_wrapper(tmp_path))))
    res = _push(r, _env(CDEBT_TTY=str(tmp_path / "no-such-terminal")))
    assert res.returncode == 0
    assert "No terminal is attached" in res.stderr and "cdebt quiz --pr" in res.stderr
    assert (
        "refs/heads/feature"
        in subprocess.run(["git", "ls-remote", str(remote)], capture_output=True, text=True).stdout
    )
    assert not notes.read_commit(r.path, git(r.path, "rev-parse", "HEAD").strip())


def test_no_verify_bypasses_the_hook_and_the_gate_catches_it(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r, _remote = _setup(make_repo, tmp_path)
    hook.apply_install(hook.plan_install(r.path, str(_wrapper(tmp_path))))
    res = _push(r, _env(CDEBT_TTY=str(tmp_path / "unused")), "--no-verify")
    assert res.returncode == 0 and "cdebt" not in res.stderr
    out = CliRunner().invoke(
        app, ["gate", "--repo", str(r.path), "--base", "origin/main", "--enforce", "--recheck"]
    )
    assert out.exit_code == 1 and "FAIL" in out.output  # CI would mark the code NOT UNDERSTOOD


def test_edit_after_understanding_makes_the_record_stale(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r, _remote = _setup(make_repo, tmp_path)
    answers = _oracle_answers(r.path, tmp_path)
    hook.apply_install(hook.plan_install(r.path, str(_wrapper(tmp_path))))
    assert _push(r, _env(CDEBT_TTY=str(answers))).returncode == 0
    assert run_gate(r.path, Config(), "origin/main").passed
    r.write("m.py", AI_CODE.replace("x + 2", "x + 3"))
    r.commit(f"AI edits inc again\n\n{AI}")
    res = run_gate(r.path, Config(), "origin/main")
    assert not res.passed
    stale = next(v for v in res.violations if v.symbol == "inc")
    assert stale.status == "stale" and stale.reason == "code_changed"


# ---- CI re-execution ----


def _understand(r: Repo, tmp_path: Path) -> str:
    answers = _oracle_answers(r.path, tmp_path)
    hook.apply_install(hook.plan_install(r.path, str(_wrapper(tmp_path))))
    assert _push(r, _env(CDEBT_TTY=str(answers))).returncode == 0
    return git(r.path, "rev-parse", "HEAD").strip()


def test_recheck_accepts_real_records_and_rejects_hand_edited_ones(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r, _remote = _setup(make_repo, tmp_path)
    sha = _understand(r, tmp_path)
    good = run_gate(r.path, Config(), "origin/main", recheck=True)
    assert good.passed and good.rechecked and good.understood

    # forge: make a recorded probe's answer agree with a WRONG user answer
    recs = notes.read_commit(r.path, sha)
    forged = []
    for rec in recs:
        p = rec.probes[0]
        wrong = next(o for o in p.options if o != p.answer)
        p.answer, p.user_answer, p.correct = wrong, wrong, True
        forged.append(rec)
    doc = {"schema": 1, "records": [json.loads(x.model_dump_json()) for x in forged]}
    git(r.path, "notes", "--ref=cdebt", "add", "-f", "-m", json.dumps(doc), sha)
    assert run_gate(r.path, Config(), "origin/main", recheck=False).passed  # trusted blindly
    bad = run_gate(r.path, Config(), "origin/main", recheck=True)
    assert not bad.passed
    assert all(v.status == "recheck_failed" for v in bad.violations)
    assert any("does not match re-execution" in (v.reason or "") for v in bad.violations)

    # forge: a verified record that holds no probes at all
    empty = [x.model_copy(update={"probes": []}) for x in recs]
    doc = {"schema": 1, "records": [json.loads(x.model_dump_json()) for x in empty]}
    git(r.path, "notes", "--ref=cdebt", "add", "-f", "-m", json.dumps(doc), sha)
    res = run_gate(r.path, Config(), "origin/main", recheck=True)
    assert not res.passed and any("no probes" in (v.reason or "") for v in res.violations)


def test_failed_quiz_is_reported_as_a_reason_about_the_code(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r, _remote = _setup(make_repo, tmp_path)
    answers = tmp_path / "wrong.txt"
    answers.write_text("A\n" * 60)  # whatever A is, most are wrong; the quiz cannot clear
    hook.apply_install(hook.plan_install(r.path, str(_wrapper(tmp_path))))
    assert _push(r, _env(CDEBT_TTY=str(answers))).returncode == 0
    res = run_gate(r.path, Config(), "origin/main")
    md = to_markdown(res, pr=12)
    assert "NOT UNDERSTOOD" in md and "`cdebt quiz --pr 12`" in md and "/cdebt recheck" in md
    assert "(advisory)" in md and "Dev" not in md and "@" not in md.split("<sub>")[0]


# ---- quiz --pr and init with a fake gh ----


def _fake_gh(tmp_path: Path, head: str, log: Path) -> Path:
    g = tmp_path / "gh"
    g.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {log}\n'
        'if [ "$1 $2" = "pr view" ]; then '
        f'echo \'{{"baseRefName":"main","headRefOid":"{head}"}}\'; fi\n'
        'if [ "$1" = "api" ]; then cat >> ' + str(log) + "; fi\n"
    )
    g.chmod(g.stat().st_mode | stat.S_IXUSR)
    return g


def test_quiz_pr_pushes_notes_and_asks_ci_to_recheck(make_repo, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r, remote = _setup(make_repo, tmp_path)
    answers = _oracle_answers(r.path, tmp_path).read_text()
    head = git(r.path, "rev-parse", "HEAD").strip()
    log = tmp_path / "gh.log"
    monkeypatch.setenv("CDEBT_GH", str(_fake_gh(tmp_path, head, log)))
    res = CliRunner().invoke(app, ["quiz", "--pr", "7", "--repo", str(r.path)], input=answers)
    assert res.exit_code == 0, res.output
    assert "notes pushed" in res.output and "asked CI to re-check" in res.output
    assert "pr comment 7 --body /cdebt recheck" in log.read_text()
    assert (
        "refs/notes/cdebt"
        in subprocess.run(["git", "ls-remote", str(remote)], capture_output=True, text=True).stdout
    )
    # the wrong branch checked out is refused
    monkeypatch.setenv("CDEBT_GH", str(_fake_gh(tmp_path, "0" * 40, log)))
    bad = CliRunner().invoke(app, ["quiz", "--pr", "7", "--repo", str(r.path)])
    assert bad.exit_code == 2 and "check out the PR branch" in bad.output


def test_init_asks_before_every_change_and_never_overwrites(
    make_repo, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", "x = 1\n")
    r.commit("a")
    git(r.path, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    log = tmp_path / "gh.log"
    monkeypatch.setenv("CDEBT_GH", str(_fake_gh(tmp_path, "x", log)))
    # answering no to everything changes nothing
    no = CliRunner().invoke(app, ["init", "--repo", str(r.path), "--github"], input="n\n" * 6)
    assert no.exit_code == 0, no.output
    assert not (r.path / ".git" / "hooks" / "pre-push").exists()
    assert not (r.path / ".cdebt" / "policy.toml").exists()
    assert not (r.path / ".github").exists() and not log.exists()
    # with consent: hook, policy, workflow, and a NEW ruleset (never a branch-protection PUT)
    yes = CliRunner().invoke(app, ["init", "--repo", str(r.path), "--yes", "--github"])
    assert yes.exit_code == 0, yes.output
    assert (r.path / ".git" / "hooks" / "pre-push").exists()
    assert "timer = true" in (r.path / ".cdebt" / "policy.toml").read_text()
    wf = (r.path / ".github" / "workflows" / "cdebt.yml").read_text()
    assert "/cdebt recheck" in wf and "COLLABORATOR" in wf
    body = log.read_text()
    assert "api -X POST repos/acme/widgets/rulesets" in body and "cdebt / gate" in body
    assert "protection" not in body and "PUT" not in body
    # running it again leaves everything as it is
    (r.path / ".cdebt" / "policy.toml").write_text("timer = false\n")
    CliRunner().invoke(app, ["init", "--repo", str(r.path), "--yes"])
    assert (r.path / ".cdebt" / "policy.toml").read_text() == "timer = false\n"


def test_hook_never_recurses_and_survives_garbage(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", "x = 1\n")
    r.commit("a")
    monkeypatch.setenv("CDEBT_IN_HOOK", "1")
    assert hook.run_pre_push(r.path, "origin", "garbage\n\n", lambda m: None) == 0
    monkeypatch.delenv("CDEBT_IN_HOOK")
    assert hook.run_pre_push(r.path, "origin", "not four fields", lambda m: None) == 0
