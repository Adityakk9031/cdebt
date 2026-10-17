"""Module-level probes: "what is the value of NAME?" for top-level constants/config.

The module is imported in the sandbox (twice, via reload, to prove stability) and the answer is
`repr(value)`. This verifies the VALUES of module-level constants only, not the behaviour of
other top-level statements (imports, class headers, decorators, registrations)."""

from __future__ import annotations

import ast
import random
import shutil
from pathlib import Path

from cdebt.models import CodeUnit, Probe
from cdebt.probes.base import copy_tree, probe_id, shuffled, temp_root
from cdebt.probes.runtime import distractors, module_info
from cdebt.sandbox import (
    BLOCKED_CALLS,
    BLOCKED_MODULES,
    _import_aliases,
    run_in_sandbox,
    screen_module_level,
)

MAX_REPR = 120


def _names(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name) and not target.id.startswith("__") and target.id != "_":
        return target.id
    return None


def _rhs_unsafe(value: ast.expr, aliases: dict[str, str]) -> bool:
    """RHS depends on the environment (os, time, random, pathlib...) or is a side-effect call."""
    for n in ast.walk(value):
        if isinstance(n, ast.Name):
            if aliases.get(n.id) in BLOCKED_MODULES or n.id in BLOCKED_CALLS:
                return True
        elif isinstance(n, (ast.Await, ast.Yield, ast.Lambda)):
            return True
    return False


def candidates(source: str) -> list[tuple[str, int, bool]]:
    """(name, line, computed) for top-level `NAME = expr` whose RHS is environment-independent.
    `computed` is False for plain literals (trivial to read off, ranked last)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    aliases = _import_aliases(tree)
    out: dict[str, tuple[int, bool]] = {}
    for n in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(n, ast.Assign):
            targets, value = list(n.targets), n.value
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            targets, value = [n.target], n.value
        if value is None or len(targets) != 1 or _rhs_unsafe(value, aliases):
            continue
        name = _names(targets[0])
        if name:
            literal = isinstance(value, ast.Constant) or (
                isinstance(value, (ast.List, ast.Tuple, ast.Dict, ast.Set))
                and not any(
                    isinstance(x, (ast.Call, ast.Name)) for x in ast.walk(value) if x is not value
                )
            )
            out[name] = (n.lineno, not literal)  # last assignment wins
    return [(k, ln, comp) for k, (ln, comp) in out.items()]


def generate(
    repo: Path, rel: str, source: str, units: list[CodeUnit], seed: int, per_unit: int = 2
) -> tuple[list[Probe], list[str]]:
    if not rel.endswith(".py"):
        return [], [f"{rel}: module-level probes are Python-only"]
    mod_unit = next((u for u in units if u.symbol == "<module>"), None)
    if mod_unit is None:
        return [], [f"{rel}: no module-level unit"]
    why = screen_module_level(source)
    if why:
        return [], [f"{rel}::<module>: skipped ({'; '.join(why[:3])})"]
    cands = candidates(source)
    if not cands:
        return [], [f"{rel}::<module>: no environment-independent top-level constants"]
    rng = random.Random(seed)
    cands.sort(key=lambda c: (not c[2], c[1]))  # computed first, then literals, by line
    names = [c[0] for c in cands][:12]
    mod, roots = module_info(repo, rel)
    root = temp_root("cdebt-const-")
    probes: list[Probe] = []
    skips: list[str] = []
    try:
        copy_tree(repo, root)
        (root / rel).write_text(source)
        res = run_in_sandbox(
            {
                "mode": "attrs",
                "module": mod,
                "names": names,
                "sys_path": [str(root / r) for r in roots],
            },
            root=root,
            timeout=15,
        )
        pl = res.payload or {}
        if res.timed_out or res.violations or "error" in pl or "values" not in pl:
            return [], [
                f"{rel}::<module>: not safely importable "
                f"({pl.get('error') or res.violations or 'timeout'})"
            ]
        values: dict[str, list[str]] = pl["values"]  # type: ignore[assignment]
        info = {c[0]: c for c in cands}
        for name in names:
            if len(probes) >= per_unit:
                break
            v1, v2 = values[name]
            if v1 != v2:
                skips.append(f"{rel}::{name}: unstable across imports")
                continue
            if v1 == "'<missing>'" or len(v1) > MAX_REPR:
                skips.append(f"{rel}::{name}: missing or too long")
                continue
            try:
                lit = ast.literal_eval(v1)
            except (ValueError, SyntaxError):
                skips.append(f"{rel}::{name}: non-literal value")
                continue
            opts = shuffled([v1, *distractors(lit, rng)[:3]], rng)
            if len(opts) < 2:
                skips.append(f"{rel}::{name}: could not build distractors")
                continue
            q = f"After importing {mod}, what is the value of `{name}`?"
            probes.append(
                Probe(
                    id=probe_id("constant", rel, "<module>", q, seed),
                    kind="constant",
                    path=rel,
                    symbol="<module>",
                    question=q,
                    options=opts,
                    answer=v1,
                    verified_by="sandbox-exec",
                    seed=seed,
                    evidence={
                        "isolation": res.isolation,
                        "name": name,
                        "line": info[name][1],
                        "computed": info[name][2],
                        "evaluations": 2,
                        "scope": "value of this constant only",
                    },
                )
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return probes, skips
