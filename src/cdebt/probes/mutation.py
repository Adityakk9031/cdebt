"""Mutation probes (Python): "if this line changed, which tests fail?" Answer = tests that
ACTUALLY fail when the mutant runs. In-house single-token mutator (see docs/PLAN.md Q6);
mutmut is not used to aim at one line. Baseline must be green or no probe is made."""

from __future__ import annotations

import ast
import io
import json
import random
import re
import shutil
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

from cdebt.models import CodeUnit, Probe
from cdebt.probes.base import NONE_OPTION, copy_tree, probe_id, shuffled, temp_root
from cdebt.sandbox import SandboxResult, run_in_sandbox

SWAP = {
    "<": "<=",
    "<=": "<",
    ">": ">=",
    ">=": ">",
    "==": "!=",
    "!=": "==",
    "+": "-",
    "-": "+",
    "*": "+",
    "//": "*",
    "and": "or",
    "or": "and",
    "True": "False",
    "False": "True",
}
_OPERAND_END = {tokenize.NAME, tokenize.NUMBER, tokenize.STRING}


@dataclass(frozen=True)
class Site:
    row: int  # 1-indexed
    col: int
    old: str
    new: str


def sites(source: str, unit: CodeUnit) -> list[Site]:
    a, b = unit.ranges[0]
    lines = source.splitlines(keepends=True)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    body_start = a
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.FunctionDef)
            and n.lineno <= a <= (n.end_lineno or 0)
            and n.body
            and n.name == unit.symbol.split(".")[-1].split("#")[0]
        ):
            body_start = n.body[0].lineno
            if isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
                body_start = (n.body[0].end_lineno or body_start) + 1  # skip docstring
    out: list[Site] = []
    prev: tokenize.TokenInfo | None = None
    toks = tokenize.generate_tokens(io.StringIO("".join(lines)).readline)
    for tk in toks:
        if tk.type in (
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.COMMENT,
            tokenize.INDENT,
            tokenize.DEDENT,
        ):
            continue
        row, col = tk.start
        if body_start <= row <= b:
            s = tk.string
            operand_before = prev is not None and (
                prev.type in _OPERAND_END or prev.string in {")", "]", "}"}
            )
            if tk.type == tokenize.OP and s in SWAP:
                if s in {"+", "-", "*"} and not operand_before:
                    pass
                else:
                    out.append(Site(row, col, s, SWAP[s]))
            elif tk.type == tokenize.NAME and s in {"and", "or", "True", "False"}:
                out.append(Site(row, col, s, SWAP[s]))
            elif tk.type == tokenize.NUMBER and re.fullmatch(r"\d+", s):
                out.append(Site(row, col, s, str(int(s) + 1)))
        prev = tk
    return out


def apply(source: str, s: Site) -> str | None:
    lines = source.splitlines(keepends=True)
    ln = lines[s.row - 1]
    if ln[s.col : s.col + len(s.old)] != s.old:
        return None
    lines[s.row - 1] = ln[: s.col] + s.new + ln[s.col + len(s.old) :]
    new = "".join(lines)
    try:
        ast.parse(new)
    except SyntaxError:
        return None
    return new if new != source else None


def test_python(repo: Path) -> str:
    v = repo / ".venv" / "bin" / "python"
    return str(v) if v.exists() else sys.executable


_FAIL = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def run_tests(
    root: Path, python: str, timeout: float, collect_only: bool = False
) -> tuple[SandboxResult, list[str]]:
    cmd = [
        python,
        "-m",
        "pytest",
        "-q",
        "--tb=no",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "-rfE",
    ] + (["--collect-only"] if collect_only else [])
    env = {"PYTHONPATH": f"{root / 'src'}:{root}", "VIRTUAL_ENV": ""}
    r = run_in_sandbox({}, root=root, timeout=timeout, policy="tests", command=cmd, extra_env=env)
    if collect_only:
        ids = [ln.strip() for ln in r.stdout.splitlines() if "::" in ln and " " not in ln.strip()]
    else:
        ids = sorted({m.split(" - ")[0] for m in _FAIL.findall(r.stdout)})
    return r, ids


def generate(
    repo: Path,
    rel: str,
    source: str,
    units: list[CodeUnit],
    seed: int,
    per_unit: int = 1,
    timeout: float = 120.0,
) -> tuple[list[Probe], list[str]]:
    if not rel.endswith(".py"):
        return [], [f"{rel}: mutation probes are Python-only in v1"]
    fns = [u for u in units if not u.symbol.startswith("<")]
    cand = {u.symbol: sites(source, u) for u in fns}
    if not any(cand.values()):
        return [], [f"{rel}: no mutable tokens in any function"]
    rng = random.Random(seed)
    python = test_python(repo)
    root = temp_root("cdebt-mut-")
    probes: list[Probe] = []
    skips: list[str] = []
    try:
        copy_tree(repo, root)
        (root / rel).write_text(source)
        base, _ = run_tests(root, python, timeout)
        if base.returncode != 0 or base.timed_out:
            return [], [
                f"{rel}: baseline test run not green (rc={base.returncode}); "
                "mutation answers would be unreliable"
            ]
        _, all_tests = run_tests(root, python, timeout, collect_only=True)
        lines = source.splitlines()
        for u in fns:
            ss = cand[u.symbol][:]
            rng.shuffle(ss)
            made = 0
            for s in ss[: per_unit * 3]:
                if made >= per_unit:
                    break
                mutated = apply(source, s)
                if mutated is None:
                    continue
                (root / rel).write_text(mutated)
                r, failed = run_tests(root, python, timeout)
                (root / rel).write_text(source)
                if r.timed_out or r.returncode not in (0, 1):
                    skips.append(f"{rel}::{u.symbol}: mutant run inconclusive (rc={r.returncode})")
                    continue
                if r.returncode == 1 and not failed:
                    skips.append(f"{rel}::{u.symbol}: failures not parseable")
                    continue
                shown_true = failed[:3]
                pool = [t for t in all_tests if t not in failed]
                wrong = rng.sample(pool, min(len(pool), max(1, 4 - len(shown_true))))
                if not shown_true and not wrong:
                    continue
                old_l = lines[s.row - 1].strip()
                new_l = mutated.splitlines()[s.row - 1].strip()
                q = (
                    f"In `{u.symbol}` ({rel}:{s.row}), the line `{old_l}` is changed to "
                    f"`{new_l}`. Which tests would fail?"
                )
                truth = shown_true or [NONE_OPTION]
                if len(failed) > 3:  # only ask when the true set is fully representable
                    continue
                probes.append(
                    Probe(
                        id=probe_id("mutation", rel, u.symbol, q, seed),
                        kind="mutation",
                        path=rel,
                        symbol=u.symbol,
                        question=q,
                        options=shuffled(shown_true + wrong + [NONE_OPTION], rng),
                        answer=json.dumps(sorted(truth)),
                        multi=True,
                        verified_by="mutation-run",
                        seed=seed,
                        evidence={
                            "isolation": r.isolation,
                            "mutation": f"{s.old!r}->{s.new!r}",
                            "line": s.row,
                            "failed_tests": failed,
                            "baseline_green": True,
                        },
                    )
                )
                made += 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return probes, skips
