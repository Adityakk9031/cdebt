"""Questions from the developer's own coding agent. See docs/METHOD.md (Agent-proposed questions).

Flow: cdebt creates a session (targets only), launches the agent headlessly with the cdebt MCP
server attached, the agent submits proposals, and cdebt RE-EXECUTES every accepted proposal itself
to get the true answer. Nothing the agent says decides correctness."""

from __future__ import annotations

import importlib.util
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from cdebt.agents import adapters
from cdebt.agents.probes import Rejected, execute, parse_proposal
from cdebt.agents.session import Session, Target, targets_for_file
from cdebt.agents.userconfig import AgentChoice
from cdebt.config import Config
from cdebt.models import Probe
from cdebt.store import Store

DEFAULT_PER_TARGET = 8
DEFAULT_TIMEOUT = 90.0
DEFAULT_BATCH = 6  # targets per agent run
MIN_QUESTIONS = 3  # verified questions a function needs before it stops being retried
MAX_ROUNDS = 2


def _run_agent(
    argv: list[str], env: dict[str, str], cwd: Path, timeout: float
) -> tuple[int | None, str]:
    """Run the agent; kill its whole process group at the deadline. Returns (exit code, tail)."""
    try:
        p = subprocess.Popen(  # noqa: S603 - argv is built by cdebt adapters, never from the repo
            argv,
            cwd=cwd,
            env=adapters.child_env(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as e:
        return None, f"could not start the agent: {e}"
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, (out or "")[-400:]
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":  # explicit so type checkers treat the rest as POSIX-only
            p.kill()
        else:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                p.kill()
        out, _ = p.communicate()
        return None, f"timed out after {timeout:.0f} s"


def _reason_summary(recs: list[dict[str, object]], top: int = 3) -> str:
    """The most common reasons proposals were rejected ("" when none were), numbers masked."""
    why = Counter(
        re.sub(r"\d+", "N", str(r["why"]))[:90] for r in recs if not r["accepted"] and r["why"]
    )
    if not why:
        return ""
    return "; rejected: " + ", ".join(f"{n}x {w}" for w, n in why.most_common(top))


def _one_round(
    repo: Path,
    cfg: Config,
    choice: AgentChoice,
    targets: list[Target],
    *,
    per_target: int,
    timeout: float,
    seed: int,
) -> tuple[list[Probe], list[str]]:
    """One agent run over `targets`; every accepted proposal is re-executed by cdebt."""
    notes: list[str] = []
    sess = Session.create(repo, choice.name, targets, per_target)
    try:
        try:
            if importlib.util.find_spec("mcp") is None:
                raise ValueError(
                    "the `mcp` library is missing; install the agent extra: "
                    'pip install "cdebt[agent]"'
                )
            run = adapters.build(choice, sess.dir, repo, per_target)
        except (FileNotFoundError, ValueError) as e:
            return [], [f"agent {choice.name}: {e}; using the builtin generator"]
        notes += run.notes
        t0 = time.monotonic()
        code, tail = _run_agent(run.argv, run.env, run.cwd, timeout)
        recs = sess.read()
        if code != 0 and not recs:
            notes.append(f"agent {choice.name} produced no proposals ({tail.strip()[-160:]})")
            return [], notes
        probes: list[Probe] = []
        seen: set[str] = set()
        accepted = 0
        by_id = {t.id: t for t in targets}
        for r in recs:
            if not r["accepted"]:
                continue
            accepted += 1
            t = by_id[str(r["target_id"])]
            try:
                p = execute(
                    repo, t.path, parse_proposal(r["spec"]),
                    cfg, proposer=f"agent:{choice.name}", seed=seed,
                )  # fmt: skip
            except Rejected as e:
                notes.append(f"{t.id}: accepted by the server but rejected on re-run: {e}")
                continue
            if p.question in seen:
                continue
            seen.add(p.question)
            probes.append(p)
        notes.append(
            f"agent {choice.name}: {len(recs)} proposals, {accepted} accepted, "
            f"{len(probes)} verified questions in {time.monotonic() - t0:.0f} s"
            + _reason_summary(recs)
        )
        return probes, notes
    finally:
        sess.close()


def propose_with_agent(
    repo: Path,
    rel: str,
    cfg: Config,
    choice: AgentChoice,
    *,
    per_target: int = DEFAULT_PER_TARGET,
    timeout: float = DEFAULT_TIMEOUT,
    seed: int = 0,
    store: Store | None = None,
    only: set[str] | None = None,
    batch: int = DEFAULT_BATCH,
    min_questions: int = MIN_QUESTIONS,
    rounds: int = MAX_ROUNDS,
) -> tuple[list[Probe], list[str]]:
    """Probes proposed by the agent AND verified by cdebt's own execution. Never raises for an
    agent failure: returns ([], [reason]) so the caller falls back to the builtin generator.

    Reach: the targets are split into batches of `batch` (one agent run each, each with its own
    `timeout`) so one slow function cannot starve the rest, and every target that ended with fewer
    than `min_questions` verified questions is offered again, up to `rounds` rounds in total.
    `only` limits the targets to those symbols (e.g. the functions a PR changed)."""
    notes: list[str] = []
    if choice.name == "builtin":
        return [], []
    targets = targets_for_file(repo, rel, cfg, store)
    if only is not None:
        targets = [t for t in targets if t.symbol in only]
    if not targets:
        return [], [f"{rel}: no unverified functions to ask about"]
    probes: list[Probe] = []
    seen: set[str] = set()
    pending = targets
    for rnd in range(1, max(1, rounds) + 1):
        for i in range(0, len(pending), max(1, batch)):
            got, n = _one_round(
                repo, cfg, choice, pending[i : i + batch],
                per_target=per_target, timeout=timeout, seed=seed,
            )  # fmt: skip
            notes += n if rnd == 1 else [f"round {rnd}: {x}" for x in n]
            if not got and any("using the builtin generator" in x for x in n):
                return probes, notes  # the agent cannot run at all: do not retry
            for p in got:
                if p.question not in seen:
                    seen.add(p.question)
                    probes.append(p)
        counts = {t.symbol: sum(1 for p in probes if p.symbol == t.symbol) for t in targets}
        pending = [t for t in targets if counts[t.symbol] < min_questions]
        if not pending:
            break
    short = len(pending)
    notes.append(
        f"{rel}: {len(targets) - short} of {len(targets)} functions reached {min_questions} "
        f"verified agent questions"
    )
    return probes, notes
